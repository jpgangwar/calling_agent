from flask import Flask, request, Response, render_template, jsonify
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import requests
from requests.auth import HTTPBasicAuth
from openai import OpenAI
from dotenv import load_dotenv
import os

# --------------------------------------------------
# Load Environment Variables
# --------------------------------------------------

load_dotenv()

app = Flask(__name__)

# --------------------------------------------------
# OpenAI
# --------------------------------------------------

openai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

# --------------------------------------------------
# Twilio
# --------------------------------------------------

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

client = Client(
    ACCOUNT_SID,
    AUTH_TOKEN
)

# --------------------------------------------------
# Configuration
# --------------------------------------------------

NGROK_URL = os.getenv("NGROK_URL")
CUSTOMER_PHONE_NUMBER = os.getenv("CUSTOMER_PHONE_NUMBER")

# --------------------------------------------------
# Store latest conversation
# --------------------------------------------------

conversation_data = {
    "status": "Ready",
    "transcript": "",
    "response": "",
    "recording": "",
    "duration": ""
}


# ==================================================
# OPENAI FUNCTIONS
# ==================================================

def transcribe(audio_file):

    with open(audio_file, "rb") as f:

        transcript = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=f
        )

    print("USER:", transcript.text)

    return transcript.text


def chat(user_text):

    response = openai_client.responses.create(
        model="gpt-5.1-2025-11-13",
        input=user_text
    )

    answer = response.output_text

    print("RIA:", answer)

    return answer


def generate_speech(text):

    output_file = "static/reply.mp3"

    with openai_client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="alloy",
        input=text
    ) as response:

        response.stream_to_file(output_file)

    return output_file


def build_audio_url():
    if not NGROK_URL:
        return None

    audio_file = os.path.join(BASE_DIR, "static", "reply.mp3")
    if not os.path.exists(audio_file):
        return None

    return f"{NGROK_URL.rstrip('/')}/static/reply.mp3"


# ==================================================
# NGROK HEADER
# ==================================================

@app.after_request
def add_ngrok_skip_header(response):

    response.headers["ngrok-skip-browser-warning"] = "true"

    return response


# ==================================================
# HOME / UI
# ==================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


# ==================================================
# DASHBOARD API
# ==================================================

@app.route("/status")
def status():

    return jsonify(conversation_data)


# ==================================================
# TWILIO VOICE WEBHOOK
# ==================================================

@app.route("/voice", methods=["GET", "POST"])
def voice():

    conversation_data["status"] = "Connected"

    response = VoiceResponse()

    response.say(
        "Hello. This is your Assistant RIA. "
        "How can I help you?",
        voice="alice"
    )

    response.record(
        max_length=300,
        timeout=5,
        play_beep=True,
        trim="trim-silence",
        action="/recording_complete",
        method="POST"
    )

    return Response(
        str(response),
        mimetype="text/xml"
    )


# ==================================================
# RECORDING COMPLETE
# ==================================================

@app.route("/recording_complete", methods=["POST"])
def recording_complete():

    recording_url = request.form.get(
        "RecordingUrl"
    )

    recording_sid = request.form.get(
        "RecordingSid"
    )

    duration = request.form.get(
        "RecordingDuration"
    )

    print("=" * 50)
    print("Recording Completed")
    print("Recording SID:", recording_sid)
    print("Recording URL:", recording_url)
    print("Duration:", duration)
    print("=" * 50)

    conversation_data["status"] = "Processing..."
    conversation_data["duration"] = duration or ""

    try:

        # ------------------------------------------
        # Download Recording
        # ------------------------------------------

        audio_response = requests.get(
            recording_url + ".mp3",
            auth=HTTPBasicAuth(
                ACCOUNT_SID,
                AUTH_TOKEN
            )
        )

        audio_response.raise_for_status()

        audio_file = "call_recording.mp3"

        with open(audio_file, "wb") as f:
            f.write(audio_response.content)

        # ------------------------------------------
        # Speech To Text
        # ------------------------------------------

        text = transcribe(
            audio_file
        )

        conversation_data["transcript"] = text

        # ------------------------------------------
        # GPT
        # ------------------------------------------

        answer = chat(text)

        conversation_data["response"] = answer

        # ------------------------------------------
        # Text To Speech
        # ------------------------------------------

        generate_speech(answer)

        # ------------------------------------------
        # Twilio Response
        # ------------------------------------------

        response = VoiceResponse()

        response.play(
            NGROK_URL +
            "/static/reply.mp3"
        )

        response.record(
            action="/recording_complete",
            method="POST",
            timeout=5,
            max_length=300
        )

        conversation_data["status"] = "Waiting for customer"

        return Response(
            str(response),
            mimetype="text/xml"
        )

    except Exception as e:

        print("ERROR:", e)

        conversation_data["status"] = "Error"

        response = VoiceResponse()

        response.say(
            "Sorry, there was a technical problem."
        )

        response.hangup()

        return Response(
            str(response),
            mimetype="text/xml"
        )


# ==================================================
# MAKE OUTBOUND CALL
# ==================================================

@app.route("/make_call", methods=["GET", "POST"])
def make_call():

    try:

        conversation_data["status"] = "Calling..."

        call = client.calls.create(

            to=CUSTOMER_PHONE_NUMBER,

            from_=TWILIO_NUMBER,

            url=NGROK_URL + "/voice"

        )

        print(
            "Call Initiated:",
            call.sid
        )

        conversation_data["status"] = "Calling"

        return jsonify({
            "success": True,
            "message": "Call Initiated",
            "call_sid": call.sid
        })

    except Exception as e:

        print("CALL ERROR:", e)

        conversation_data["status"] = "Call Failed"

        return jsonify({
            "success": False,
            "error": str(e)
        })


@app.route("/reset", methods=["GET", "POST"])
def reset():

    conversation_data["status"] = "Ready"
    conversation_data["transcript"] = ""
    conversation_data["response"] = ""
    conversation_data["recording"] = ""
    conversation_data["duration"] = ""

    return jsonify({
        "success": True
    })


# ==================================================
# RUN
# ==================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )