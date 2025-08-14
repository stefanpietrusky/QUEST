"""
title: QUEST V1 [QUESTION UNDERSTANDING, EVALUATION AND SPEECH TRAINING]
author: stefanpietrusky
author_url: https://downchurch.studio/
version: 1.0
"""

from flask import Flask, request, jsonify, Response, send_from_directory
import os, subprocess, re, asyncio, time, logging, markdown
import whisper
from werkzeug.utils import secure_filename
from bs4 import BeautifulSoup

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

logging.basicConfig(level=logging.DEBUG)

whisper_model = whisper.load_model("base")

question_count = 0
current_question = ""
asked_questions = set()

def query_llm_via_ollama(input_text):
    try:
        process = subprocess.run(
            ["ollama", "run", "llama3.2"],
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            timeout=40
        )
        if process.returncode != 0:
            return f"Fehler bei der Modellanfrage: {process.stderr.strip()}"
        response = re.sub(r'\x1b\[.*?m', '', process.stdout)
        return response.strip()
    except subprocess.TimeoutExpired:
        return "Zeitüberschreitung bei der Modellanfrage."
    except Exception as e:
        return f"Ein unerwarteter Fehler ist aufgetreten: {str(e)}"

def markdown_to_text(md):
    html = markdown.markdown(md)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator='\n')
    return text.strip()

def save_to_file(filename, content):
    with open(filename, "a") as f:
        f.write(content + "\n")

def reset_question_state():
    global current_question
    current_question = ""

def clean_question(question: str) -> str:
    return question.strip().strip('\'"')

def generate_topic_question(topic, language):
    if not topic or not topic.strip():
        raise ValueError("Kein Thema angegeben.")
    global question_count, current_question, asked_questions
    reset_question_state()
    question_count += 1

    prompts = {
        "de": f"Erzeuge eine einfache, natürliche Frage über {topic}, die ein Kunde, Patient oder Gesprächspartner stellen könnte. Die Frage soll konkret beantwortbar sein und natürlich formuliert.",
        "en": f"Generate a single, direct question about {topic} that a customer, patient, or conversation partner might ask. The question should be phrased naturally and require a concrete answer.",
        "fr": f"Génère une question simple et directe sur {topic} qu'un client, un patient ou un interlocuteur pourrait poser. La question doit être formulée naturellement et demander une réponse concrète."
    }

    question_prompt = prompts.get(language, prompts["de"])

    while True:
        generated_question = query_llm_via_ollama(question_prompt)
        cleaned_question = clean_question(generated_question)
        if cleaned_question not in asked_questions:
            asked_questions.add(cleaned_question)
            current_question = cleaned_question
            break 

    save_to_file("questions_log.txt", f"Frage {question_count}: {current_question}")
    return current_question

async def convert_text_to_speech(text, prefix="ai_feedback", output_file=None, voice="en-US-JennyNeural"):
    if output_file is None:
        output_file = f"{prefix}_{question_count}_{int(time.time())}.mp3"
    output_path = os.path.join(app.config["UPLOAD_FOLDER"], output_file)
    import edge_tts
    tts = edge_tts.Communicate(text, voice=voice)
    await tts.save(output_path)
    return output_file 

def clear_all():
    global question_count, current_question, asked_questions
    for filename in os.listdir(app.config["UPLOAD_FOLDER"]):
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            app.logger.error(f"Fehler beim Löschen der Datei {filename}: {e}")
    question_count = 0
    current_question = ""
    asked_questions = set()
    return "", "", ""

async def start_process(topic, language):
    question = generate_topic_question(topic, language)
    voice_map = {
        "de": "de-DE-KatjaNeural",
        "en": "en-US-JennyNeural",
        "fr": "fr-FR-DeniseNeural"
    }
    voice = voice_map.get(language, "en-US-JennyNeural")
    audio_file = await convert_text_to_speech(question, f"customer_question_{question_count}", voice=voice)
    return {"question": question, "audio": audio_file}

def transcribe_audio_whisper(audio_file_path, language):
    try:
        result = whisper_model.transcribe(audio_file_path, language=language)
        transcription = result.get("text", "").strip()
        return transcription if transcription else "Keine Erkennung möglich."
    except Exception as e:
        app.logger.error(f"Whisper Transkriptionsfehler: {e}")
        return f"Fehler bei der Transkription: {str(e)}"

@app.route('/transcribe', methods=["POST"])
def transcribe_route():
    if "audio" not in request.files:
        return jsonify({"error": "Keine Audiodatei übermittelt."}), 400

    audio_file = request.files["audio"]
    filename = secure_filename(audio_file.filename)
    if not filename:
        return jsonify({"error": "Ungültiger Dateiname."}), 400

    temp_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    try:
        audio_file.save(temp_path)
        language = request.form.get("language", "de")
        transcription = transcribe_audio_whisper(temp_path, language)
        return jsonify({
            "transcription": transcription,
            "saved_audio": filename
        })
    except Exception as e:
        app.logger.exception("Transkriptionsfehler")
        return jsonify({"error": f"Fehler bei der Transkription: {str(e)}"}), 500

async def get_feedback(transcribed_response, language):
    global current_question
    prompts = {
        "de": (
            f"Frage: {current_question}\n"
            f"Antwort des Schülers: {transcribed_response}\n\n"
            "Bitte gib ein strukturiertes Feedback nach den GER-Kriterien für mündliche Sprachkompetenz. "
            "Formatiere die Ausgabe in Markdown ohne Meta-Kommentare. "
            "Beinhaltet die Abschnitte:\n\n"
            "**Genauigkeit:** (Grammatik und Wortschatz)\n"
            "**Flüssigkeit:** (Sprachfluss und Ausdrucksfähigkeit)\n"
            "**Interaktion:** (Reaktionsfähigkeit und Engagement)\n"
            "**Kohärenz:** (Logische Struktur und Klarheit)\n"
            "**Umfang:** (Vielfalt der Ausdrücke und Wortschatzerweiterung)\n"
            "**Gesamtniveau nach GER:** (A1, A2, B1, B2, C1 oder C2)\n"
            "**Verbesserungsvorschläge:** (konkrete Tipps zur Verbesserung)\n\n"
        ),
        "en": (
            f"Question: {current_question}\n"
            f"Student's response: {transcribed_response}\n\n"
            "Please provide structured feedback according to the CEFR criteria for oral language proficiency. "
            "Format the output in Markdown without meta commentary. "
            "Include sections on:\n\n"
            "**Accuracy:** (Grammar and vocabulary)\n"
            "**Fluency:** (Flow of speech and ease of expression)\n"
            "**Interaction:** (Ability to respond and engagement)\n"
            "**Coherence:** (Logical structure and clarity)\n"
            "**Range:** (Variety of expressions and vocabulary expansion)\n"
            "**Overall CEFR level:** (A1, A2, B1, B2, C1, or C2)\n"
            "**Improvement suggestions:** (specific tips for improvement)\n\n"
        ),
        "fr": (
            f"Question : {current_question}\n"
            f"Réponse de l'étudiant : {transcribed_response}\n\n"
            "Veuillez fournir un retour structuré selon les critères du CECR pour la compétence orale. "
            "Formatez la sortie en Markdown sans commentaire méta. "
            "Incluez les sections suivantes :\n\n"
            "**Précision :** (Grammaire et vocabulaire)\n"
            "**Fluidité :** (Flux de parole et aisance d'expression)\n"
            "**Interaction :** (Capacité à répondre et engagement)\n"
            "**Cohérence :** (Structure logique et clarté)\n"
            "**Étendue :** (Variété des expressions et enrichissement du vocabulaire)\n"
            "**Niveau global CECR :** (A1, A2, B1, B2, C1, ou C2)\n"
            "**Suggestions d'amélioration :** (conseils spécifiques pour l'amélioration)\n\n"
        )
    }

    feedback_prompt = prompts.get(language, prompts["en"])
    feedback = query_llm_via_ollama(feedback_prompt)
    
    save_to_file("responses_log.txt", f"Antwort auf Frage {question_count}: {transcribed_response}")
    save_to_file("feedback_log.txt", f"Feedback für Frage {question_count}: {feedback}")

    plain_feedback = markdown_to_text(feedback)

    voice_map = {
        "de": "de-DE-KatjaNeural",
        "en": "en-US-AriaNeural",
        "fr": "fr-FR-DeniseNeural"
    }
    voice = voice_map.get(language, "en-US-AriaNeural")
    
    audio_file = await convert_text_to_speech(plain_feedback, f"ai_feedback_{question_count}", voice=voice)
    return {"feedback": feedback, "audio": audio_file}

@app.route('/')
def index():
    return Response(HTML_CONTENT, mimetype="text/html")

@app.route('/styles.css')
def styles():
    return Response(CSS_CONTENT, mimetype="text/css")

@app.route('/script.js')
def script():
    return Response(JS_CONTENT, mimetype="application/javascript")

@app.route('/generate_question', methods=["POST"])
def generate_question_route():
    data = request.get_json() or {}
    topic = (data.get("topic") or "").strip()
    language = data.get("language", "de")

    if not topic:
        return jsonify({"error": "Bitte gib zuerst ein Thema ein."}), 400

    try:
        result = asyncio.run(start_process(topic, language))
        return jsonify(result)
    except Exception as e:
        app.logger.exception("Fehler bei der Fragenerzeugung")
        return jsonify({"error": f"Fehler bei der Fragenerzeugung: {str(e)}"}), 500

@app.route('/feedback', methods=['POST'])
def feedback():
    data = request.get_json() or {}
    transcription = (data.get("transcription") or "").strip()
    language = data.get("language", "de")

    if not transcription:
        return jsonify({"error": "Keine Antwort zum Bewerten übermittelt."}), 400
    if not current_question:
        return jsonify({"error": "Es wurde noch keine Frage gestellt."}), 400

    try:
        result = asyncio.run(get_feedback(transcription, language))
        return jsonify(result)
    except Exception as e:
        app.logger.exception("Feedback-Fehler")
        return jsonify({"error": f"Fehler beim Erzeugen des Feedbacks: {str(e)}"}), 500

@app.route('/clear', methods=["POST"])
def clear():
    cleared = clear_all()
    return jsonify({
        "question": cleared[0],
        "transcription": cleared[1],
        "feedback": cleared[2],
        "topic": ""
    })

@app.route('/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

HTML_CONTENT = """
<html lang="de">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QUEST V1 mit Mehrsprachigkeit</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <div class="container">
    <h1>QUEST V1</h1>

    <div id="language-selector" style="text-align:center; margin-bottom:20px;">
      <span class="lang-flag" data-lang="de" style="cursor:pointer;">🇩🇪</span>
      <span class="lang-flag" data-lang="en" style="cursor:pointer;">🇬🇧</span>
      <span class="lang-flag" data-lang="fr" style="cursor:pointer;">🇫🇷</span>
    </div>

    <p class="center-text" id="info-text">Klicken Sie auf <strong>Frage generieren</strong>, um eine Frage zu erhalten. Hören Sie sich die Frage an und beantworten Sie diese über das Mikrofon oder laden Sie eine Audiodatei hoch.</p>

    <div class="section">
      <input type="text" id="topic-input" placeholder="Thema eingeben (z.B. Reisen, IT, Medizin ...)" />
      <div id="topic-error" class="error-box" style="display:none;" aria-live="polite"></div>
      <button id="question-btn">Frage generieren</button>
      <div id="question-spinner" class="spinner" style="display:none;"></div>
      <textarea id="question-output" placeholder="Frage des virtuellen Kunden" readonly oninput="autoResize(this)"></textarea>
      <audio id="question-audio" controls style="display:none;"></audio>
    </div>

    <div class="section">
      <h2 id="recording-header">Ihre Aufnahme</h2>
      <div class="button-row">
        <button id="start-recording-btn">Aufnahme starten</button>
        <button id="stop-recording-btn" disabled>Aufnahme stoppen</button>
      </div>
      <div id="audio-spinner" class="spinner" style="display:none;"></div>
      <audio id="recorded-audio" controls style="display:none;"></audio>
    </div>

    <div class="section">
      <p id="upload-note"><strong>Hinweis:</strong> Bitte sprechen Sie <strong>klar und deutlich</strong>.</p>
      <input type="file" id="audio-input" accept="audio/*" style="display:none;" />
      <div class="button-row">
        <label for="audio-input" class="custom-file-upload">Audiodatei hochladen</label>
        <button id="transcribe-btn">Antwort transkribieren</button>
      </div>
      <div id="transcribe-error" class="error-box" style="display:none;" aria-live="polite"></div>
      <input type="text" id="transcribed-output"
            placeholder="Transkribierte Antwort (bearbeitbar)" />
    </div>

    <div class="section">
      <h2 id="feedback-header">Feedback</h2>
      <button id="feedback-btn">Feedback erhalten</button>
      <div id="feedback-error" class="error-box" style="display:none;" aria-live="polite"></div>
      <div id="feedback-spinner" class="spinner" style="display:none;"></div>
      <div id="feedback-output" class="formatted-feedback"></div>
      <audio id="feedback-audio" controls style="display:none;"></audio>
    </div>

    <div class="section">
      <button id="clear-btn">Zurücksetzen</button>
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="/script.js"></script>
</body>
</html>
"""

CSS_CONTENT = """
body {
  font-family: Arial, sans-serif;
  background-color: #ffffff;
  margin: 0;
  padding: 20px;
}

:root{
  --space-1: 6px;
  --space-2: 10px;
  --space-3: 16px; 
  --space-4: 24px;
  --space-5: 32px;
}

:root{
  --focus: #00B0F0; 
  --focus-ring: rgba(0,176,240,.28);
}

input[type="text"]:focus,
textarea:focus,
button:focus,
.custom-file-upload:focus {
  outline: none;     
  border-color: var(--focus);
  box-shadow: 0 0 0 4px var(--focus-ring);
  transition: border-color .15s, box-shadow .15s;
}

.container {
  width: 90%;
  max-width: 800px;
  margin: auto;
  background: white;
  padding: 20px;
  border-radius: 8px;
  box-shadow: 0 0 10px rgba(0,0,0,0.1);
  border: 3px solid #262626;
}

h1, h2 {
  text-align: center;
  color: #262626;
}

h1 { margin-block: 0 var(--space-4); }
h2 { margin-block: var(--space-3) var(--space-2); }

.section {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-3);
  margin-block: var(--space-4);
  text-align: center;
  color: #262626;
}

.section button,
.section label.custom-file-upload {
  align-self: center; 
  width: auto;  
  min-width: 120px; 
  display: inline-block;
  margin: 10px 6px;
}

input[type="text"], input[type="file"], textarea {
  width: 100%;
  padding: 10px;
  margin: 0; 
  border: 3px solid #262626;
  border-radius: 5px;
  box-sizing: border-box;
}

button {
  display: inline-block; 
  padding: 10px 20px;
  margin: 10px 5px; 
  border: none;
  border-radius: 5px;
  background-color: #ffffff;
  border: 3px solid #262626;
  color: #262626;
  cursor: pointer;
  font-size: 1em;
  transition: background-color 0.3s ease;
  width: auto; 
  min-width: 120px;
  text-align: center;
}

button:hover {
  background-color: #262626;
  border: 3px solid #262626;
  color: #ffffff;
}

button.active-recording {
  background-color: #FF5050;
  border: 3px solid #262626;
  color: #262626;
  font-weight: bold;
}

audio {
  margin: 0; 
  width: 100%;
}

.custom-file-upload {
  display: inline-block;
  padding: 10px 20px;
  margin: 0; 
  border: 3px solid #262626;
  background-color: #ffffff;
  color: #262626;
  border-radius: 5px;
  cursor: pointer;
  font-size: 1em;
  transition: background-color 0.3s ease;
  text-align: center;
}

.custom-file-upload:hover {
  background-color: #00B0F0;
  border: 3px solid #262626;
  color: #262626;
  font-weight: bold;
}

.center-text {
  text-align: center;
  color: #262626;
  margin-block: var(--space-2) var(--space-3);
}

#question-output,
#topic-input,
#feedback-output,
#transcribed-output {
  font-family: Arial, sans-serif;
  font-size: 1em;
  overflow: hidden;
  resize: none;
  min-height: 50px;
  text-align: left;
}

#feedback-output {
  margin: var(--space-2) 0 var(--space-3);
}

textarea {
  overflow: hidden;
  text-align: left;
  min-height: 50px;
  max-height: 500px;
}

.spinner {
  border: 4px solid #262626;     
  border-top: 4px solid #00B0F0;    
  border-radius: 50%;
  width: 30px;
  height: 30px;
  animation: spin 1s linear infinite;
  margin: var(--space-2) auto;              
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

#question-audio,
#feedback-audio,
#recorded-audio {
  width: 100%;
  max-width: 400px; 
  display: block; 
  margin: 0 auto; 
  background-color: #ffffff;
  border: 3px solid #262626; 
  border-radius: 10px; 
  padding: 5px; 
}

#recorded-audio { border: 2px solid #262626; }

#question-audio::-webkit-media-controls-panel {
  background-color: #ffffff;
}

.formatted-feedback {
  background-color: #ffffff;
  border: 3px solid #262626;
  padding: 10px;
  border-radius: 5px;
  white-space: normal; 
  font-size: 1em;
  text-align: justify;
  color: #333;
  line-height: 1;
}

.formatted-feedback p,
.formatted-feedback h1,
.formatted-feedback h2,
.formatted-feedback h3,
.formatted-feedback h4,
.formatted-feedback h5,
.formatted-feedback h6 {
    margin: 0;         
    margin-bottom: 0.75em;     
}

.lang-flag {
  cursor: pointer;
  font-size: 2.5rem;
  margin: 0 10px;
  transition: font-size 0.3s ease;
  user-select: none;
}

.lang-flag.selected {
  font-size: 3.5rem;
}

.lang-flag:not(.selected) {
  font-size: 2rem; 
}

.error-box {
  border: 3px solid #FF5050;
  color: #FF5050;
  background: #ffffff;
  padding: 10px;
  border-radius: 5px;
  margin: var(--space-2) 0 var(--space-3);
  text-align: left;
  font-weight: bold;
}

.button-row {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  justify-content: center;
}
"""

JS_CONTENT = """

document.addEventListener('DOMContentLoaded', function() {
  const questionBtn = document.getElementById('question-btn');
  const questionOutput = document.getElementById('question-output');
  const questionAudio = document.getElementById('question-audio');

  const transcribedOutput = document.getElementById('transcribed-output');
  const startRecordingBtn = document.getElementById('start-recording-btn');
  const stopRecordingBtn = document.getElementById('stop-recording-btn');
  const recordedAudio = document.getElementById('recorded-audio');
  const audioInput = document.getElementById('audio-input');
  const transcribeBtn = document.getElementById('transcribe-btn');
  const audioSpinner = document.getElementById('audio-spinner');
  const feedbackBtn = document.getElementById('feedback-btn');
  const feedbackOutput = document.getElementById('feedback-output');
  const feedbackAudio = document.getElementById('feedback-audio');
  const clearBtn = document.getElementById('clear-btn');

  let selectedLanguage = 'de';

  document.querySelectorAll('.lang-flag').forEach(icon => {
    icon.classList.remove('selected');
    if(icon.getAttribute('data-lang') === selectedLanguage) {
      icon.classList.add('selected');
    }
    icon.addEventListener('click', () => {
      selectedLanguage = icon.getAttribute('data-lang');
      document.querySelectorAll('.lang-flag').forEach(i => i.classList.remove('selected'));
      icon.classList.add('selected');
      updateUIText(selectedLanguage);
      if (useWebSpeech && recognition) {
        recognition.lang = selectedLanguage === 'de' ? 'de-DE' : selectedLanguage === 'fr' ? 'fr-FR' : 'en-US';
      }
    });
  });

  const translations = {
    de: {
      generate_question: "Frage generieren",
      start_recording: "Aufnahme starten",
      stop_recording: "Aufnahme stoppen",
      transcribe: "Antwort transkribieren",
      feedback: "Feedback erhalten",
      clear: "Zurücksetzen",
      info_text: "Klicken Sie auf <strong>Frage generieren</strong>, um eine Frage zu erhalten. Hören Sie sich die Frage an und beantworten Sie diese über das Mikrofon oder laden Sie eine Audiodatei hoch.",
      recording_header: "Ihre Aufnahme",
      upload_note: "<strong>Hinweis:</strong> Bitte sprechen Sie <strong>klar und deutlich</strong>.",
      feedback_header: "Feedback",
      placeholder_topic: "Thema eingeben (z.B. Reisen, IT, Medizin ...)",
      placeholder_question: "Frage des virtuellen Kunden",
      placeholder_transcribed: "Transkribierte Antwort des Studenten (bearbeitbar)"
    },
    en: {
      generate_question: "Generate question",
      start_recording: "Start recording",
      stop_recording: "Stop recording",
      transcribe: "Transcribe answer",
      feedback: "Get feedback",
      clear: "Clear",
      info_text: "Click <strong>Generate question</strong> to get a question. Listen and answer using the microphone or upload an audio file.",
      recording_header: "Your recording",
      upload_note: "<strong>Note:</strong> Please speak clearly.",
      feedback_header: "Feedback",
      placeholder_topic: "Enter topic (e.g., travel, IT, medicine ...)",
      placeholder_question: "Virtual customer's question",
      placeholder_transcribed: "Transcribed student's response (editable)"
    },
    fr: {
      generate_question: "Générer la question",
      start_recording: "Démarrer l'enregistrement",
      stop_recording: "Arrêter l'enregistrement",
      transcribe: "Transcrire la réponse",
      feedback: "Obtenir un retour",
      clear: "Réinitialiser",
      info_text: "Cliquez sur <strong>Générer la question</strong> pour obtenir une question. Écoutez et répondez avec le microphone ou téléchargez un fichier audio.",
      recording_header: "Votre enregistrement",
      upload_note: "<strong>Remarque :</strong> Veuillez parler clairement.",
      feedback_header: "Retour",
      placeholder_topic: "Entrez le sujet (par ex. voyage, informatique, médecine ...)",
      placeholder_question: "Question du client virtuel",
      placeholder_transcribed: "Réponse transcrite de l'étudiant (modifiable)"
    }
  };

  function updateUIText(lang) {
    document.getElementById('question-btn').textContent = translations[lang].generate_question;
    document.getElementById('start-recording-btn').textContent = translations[lang].start_recording;
    document.getElementById('stop-recording-btn').textContent = translations[lang].stop_recording;
    document.getElementById('transcribe-btn').textContent = translations[lang].transcribe;
    document.getElementById('feedback-btn').textContent = translations[lang].feedback;
    document.getElementById('clear-btn').textContent = translations[lang].clear;
    document.getElementById('info-text').innerHTML = translations[lang].info_text;
    document.getElementById('recording-header').textContent = translations[lang].recording_header;
    document.getElementById('upload-note').innerHTML = translations[lang].upload_note;
    document.getElementById('feedback-header').textContent = translations[lang].feedback_header;
    document.getElementById('topic-input').placeholder = translations[lang].placeholder_topic;
    document.getElementById('question-output').placeholder = translations[lang].placeholder_question;
    document.getElementById('transcribed-output').placeholder = translations[lang].placeholder_transcribed;
  }

  let useWebSpeech = false;
  let recognition;
  if ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = selectedLanguage;  
    useWebSpeech = true;
    console.log("Web Speech API aktiviert");
  } else {
    console.log("Web Speech API nicht verfügbar, verwende MediaRecorder.");
  }

  audioInput.addEventListener('change', function() {
    const file = this.files[0];
    if (file) {
      const url = URL.createObjectURL(file);
      recordedAudio.src = url;
      recordedAudio.style.display = 'block';
    }
  });

let topicErrorTimeout;

function showTopicError(message) {
  const box = document.getElementById('topic-error');
  const input = document.getElementById('topic-input');

  if (topicErrorTimeout) clearTimeout(topicErrorTimeout);

  box.textContent = message;
  box.style.display = 'block';
  input.classList.add('input-error');

  topicErrorTimeout = setTimeout(() => {
    box.style.display = 'none';
    input.classList.remove('input-error');
  }, 4000);
}

  document.getElementById('topic-input').addEventListener('input', () => {
    const box = document.getElementById('topic-error');
    box.style.display = 'none';
    document.getElementById('topic-input').classList.remove('input-error');
    if (topicErrorTimeout) clearTimeout(topicErrorTimeout);
  });

  questionBtn.addEventListener('click', function () {
    const topicInput = document.getElementById('topic-input');
    const errorBox = document.getElementById('topic-error');
    const topic = (topicInput.value || "").trim();

    errorBox.style.display = 'none';
    errorBox.textContent = '';
    topicInput.classList.remove('input-error');

    if (!topic) {
      showTopicError(
        (selectedLanguage === 'de')
          ? "Bitte gib zuerst ein Thema ein."
          : (selectedLanguage === 'fr')
            ? "Veuillez d’abord saisir un sujet."
            : "Please enter a topic first."
      );
      return;
    }

    document.getElementById('question-spinner').style.display = 'block';

    fetch('/generate_question', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: topic, language: selectedLanguage })
    })
      .then(response => {
        if (!response.ok) {
          return response.json().then(err => { throw err; });
        }
        return response.json();
      })
      .then(data => {
        questionOutput.value = data.question || '';
        autoResize(questionOutput);

        if (data.audio) {
          questionAudio.src = "/audio/" + data.audio;
          questionAudio.style.display = 'block';
          questionAudio.play();
        }
      })
      .catch(err => {
        showTopicError(
          (err && err.error)
            ? err.error
            : (selectedLanguage === 'de')
              ? "Unbekannter Fehler bei der Fragenerzeugung."
              : (selectedLanguage === 'fr')
                ? "Erreur inconnue lors de la génération de la question."
                : "Unknown error while generating the question."
        );
      })
      .finally(() => {
        document.getElementById('question-spinner').style.display = 'none';
      });
  });

  transcribeBtn.addEventListener('click', function() {
    const errId = 'transcribe-error';
    let file = audioInput.files[0];
    if (!file) {
      showBox(errId,
        selectedLanguage === 'de' ? "Bitte laden Sie eine Audiodatei hoch oder verwenden Sie die Aufnahmefunktion." :
        selectedLanguage === 'fr' ? "Veuillez télécharger un fichier audio ou utilisez la fonction d'enregistrement." :
        "Please upload an audio file or use the recording feature."
      );
      return;
    }

    let formData = new FormData();
    formData.append('audio', file);
    formData.append('language', selectedLanguage);

    audioSpinner.style.display = 'block';
    fetch('/transcribe', { method: 'POST', body: formData })
      .then(r => r.ok ? r.json() : r.json().then(e => { throw e; }))
      .then(data => {
        if (data.error) throw data;
        transcribedOutput.value = data.transcription || '';
        if (data.saved_audio) {
          recordedAudio.src = "/audio/" + data.saved_audio;
          recordedAudio.style.display = 'block';
          recordedAudio.play();
        }
      })
      .catch(err => {
        showBox(errId, err?.error || (
          selectedLanguage === 'de' ? "Fehler bei der Transkription." :
          selectedLanguage === 'fr' ? "Erreur lors de la transcription." :
          "Transcription error."
        ));
      })
      .finally(() => { audioSpinner.style.display = 'none'; });
  });

  let mediaRecorder;
  let recordedChunks = [];
  
  startRecordingBtn.addEventListener('click', async () => {
    if (useWebSpeech) {
      recognition.start();
      startRecordingBtn.disabled = true;
      stopRecordingBtn.disabled = false;
      startRecordingBtn.classList.add("active-recording");
      startRecordingBtn.textContent = "Aufnahme läuft...";
    } else {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        recordedChunks = [];
        mediaRecorder.ondataavailable = function(event) {
          if (event.data.size > 0) {
            recordedChunks.push(event.data);
          }
        };
        mediaRecorder.onstop = function() {
          const blob = new Blob(recordedChunks, { type: 'audio/webm' });
          const formData = new FormData();
          formData.append('audio', blob, 'recorded_audio.webm');
          audioSpinner.style.display = 'block';
          fetch('/transcribe', { method: 'POST', body: formData })
            .then(response => response.json())
            .then(data => {
              transcribedOutput.value = data.transcription;
              recordedAudio.src = "/audio/" + data.saved_audio;
              recordedAudio.style.display = 'block';
              recordedAudio.play();
            })
            .catch(err => console.error(err))
            .finally(() => {
              audioSpinner.style.display = 'none';
            });
        };
        mediaRecorder.start();
        startRecordingBtn.disabled = true;
        stopRecordingBtn.disabled = false;
        startRecordingBtn.classList.add("active-recording");
        startRecordingBtn.textContent = "Aufnahme läuft...";
      } catch (err) {
        console.error("Fehler beim Zugriff auf das Mikrofon:", err);
      }
    }
  });
  
  stopRecordingBtn.addEventListener('click', () => {
    if (useWebSpeech) {
      recognition.stop();
      startRecordingBtn.disabled = false;
      stopRecordingBtn.disabled = true;
      startRecordingBtn.classList.remove("active-recording");
      startRecordingBtn.textContent = "Aufnahme starten";
    } else {
      if (mediaRecorder) {
        mediaRecorder.stop();
        startRecordingBtn.disabled = false;
        stopRecordingBtn.disabled = true;
        startRecordingBtn.classList.remove("active-recording");
        startRecordingBtn.textContent = "Aufnahme starten";
      }
    }
  });
  
  if (useWebSpeech) {
    recognition.onresult = function(event) {
      let transcript = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      transcribedOutput.value = transcript;
    };
    recognition.onerror = function(event) {
      console.error("Spracherkennungsfehler:", event.error);
    };
    recognition.onend = function() {
      startRecordingBtn.disabled = false;
      stopRecordingBtn.disabled = true;
    };
  }
  
  transcribeBtn.addEventListener('click', function() {
    let file = audioInput.files[0];
    if (!file) {
      alert("Bitte laden Sie eine Audiodatei hoch oder verwenden Sie die Aufnahmefunktion.");
      return;
    }
    let formData = new FormData();
    formData.append('audio', file);

    audioSpinner.style.display = 'block';

    fetch('/transcribe', { method: 'POST', body: formData })
      .then(response => response.json())
      .then(data => {
        transcribedOutput.value = data.transcription;
        if (data.saved_audio) {
          recordedAudio.src = "/audio/" + data.saved_audio;
          recordedAudio.style.display = 'block';
          recordedAudio.play();
        }
      })
      .catch(err => console.error(err))
      .finally(() => {
        audioSpinner.style.display = 'none';
      });
  });

  feedbackBtn.addEventListener("click", function () {
    const transcription = (transcribedOutput.value || "").trim();

    if (!transcription) {
      showBox(
        'feedback-error',
        selectedLanguage === 'de'
          ? "Bitte transkribieren Sie zuerst die Antwort."
          : selectedLanguage === 'fr'
            ? "Veuillez d’abord transcrire la réponse."
            : "Please transcribe the answer first."
      );
      document.getElementById('transcribed-output').focus();
      return;
    }

    document.getElementById("feedback-spinner").style.display = "block";

    fetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcription: transcription, language: selectedLanguage }),
    })
      .then(r => r.ok ? r.json() : r.json().then(e => { throw e; }))
      .then(data => {
        if (data.error) throw data;

        showBox('feedback-error', '', ''); 
        const box = document.getElementById('feedback-error');
        if (box) box.style.display = 'none';

        feedbackOutput.innerHTML = marked.parse(data.feedback || '');
        if (data.audio) {
          feedbackAudio.src = "/audio/" + data.audio;
          feedbackAudio.style.display = "block";
          feedbackAudio.play();
        }
      })
      .catch(err => {
        showBox(
          'feedback-error',
          err?.error ||
            (selectedLanguage === 'de'
              ? "Fehler beim Erzeugen des Feedbacks."
              : selectedLanguage === 'fr'
                ? "Erreur lors de la génération du retour."
                : "Error generating feedback."),
          'transcribed-output'
        );
      })
      .finally(() => {
        document.getElementById("feedback-spinner").style.display = "none";
      });
  });

  clearBtn.addEventListener('click', function() {
    fetch('/clear', { method: 'POST' })
      .then(response => response.json())
      .then(data => {
        questionOutput.value = data.question;
        transcribedOutput.value = data.transcription;
        feedbackOutput.innerHTML = data.feedback || "";
        questionAudio.style.display = 'none';
        feedbackAudio.style.display = 'none';
        recordedAudio.src = "";
        recordedAudio.style.display = "none";
        document.getElementById('topic-input').value = data.topic;
      })
      .catch(console.error);
  });

  function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = textarea.scrollHeight + 'px';
  }

  function showBox(id, message, inputId) {
    const box = document.getElementById(id);
    if (!box) return;
    if (box._t) clearTimeout(box._t);
    box.textContent = message;
    box.style.display = 'block';
    if (inputId) document.getElementById(inputId)?.classList.add('input-error');
    box._t = setTimeout(() => {
      box.style.display = 'none';
      if (inputId) document.getElementById(inputId)?.classList.remove('input-error');
    }, 4000);
  }
});
"""

if __name__ == '__main__':
    app.run(debug=True)
