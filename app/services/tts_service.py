"""
TTS Service — cadeia de 3 provedores para Saori (Gemini > Google Cloud > OpenAI).

USADO APENAS pela Saori (endpoint /tts). O chat usa OpenAI direto.

Cadeia de fallback:
  1. Gemini 2.5 Flash TTS (voz Aoede feminina, ultra-realista)
  2. Google Cloud Neural2 (pt-BR-Neural2-C, feminina)
  3. OpenAI TTS (tts-1-hd, voz onyx) — último recurso
"""

from __future__ import annotations

import base64
import io
import logging
import wave

from app.config import settings

logger = logging.getLogger(__name__)


async def generate_tts(text: str, max_chars: int = 5000) -> str | None:
    """Converte texto em audio base64 usando cadeia de fallback.

    Args:
        text: Texto para converter em audio.
        max_chars: Limite de caracteres (evita timeout em textos longos).

    Returns:
        Audio em base64 (WAV para Gemini, MP3 para Google/OpenAI) ou None se todos falharem.
    """
    if not text or not text.strip():
        return None

    text = text[:max_chars]
    audio_b64 = None

    # 1. Gemini TTS (voz ultra-realista com emocoes)
    if settings.tts.provider == "gemini":
        audio_b64 = _try_gemini_tts(text)

    # 2. Google Cloud TTS (Neural2 fallback)
    if audio_b64 is None and settings.tts.provider in ("google", "gemini"):
        audio_b64 = _try_google_cloud_tts(text)

    # 3. Fallback OpenAI
    if audio_b64 is None and settings.tts.openai_api_key:
        audio_b64 = await _try_openai_tts(text)

    return audio_b64


def _try_gemini_tts(text: str) -> str | None:
    """Tenta gerar audio via Gemini 2.5 Flash TTS."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=settings.tts.gemini_project,
            location=settings.tts.gemini_location,
        )
        response = client.models.generate_content(
            model=settings.tts.gemini_model,
            contents=f"Fale em portugues brasileiro, de forma natural e expressiva: {text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=settings.tts.gemini_voice,
                        )
                    )
                ),
            ),
        )
        # Audio retorna como PCM 24kHz 16-bit mono
        pcm_data = response.candidates[0].content.parts[0].inline_data.data
        # Converter PCM pra WAV em memoria
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm_data)
        audio_b64 = base64.b64encode(wav_buffer.getvalue()).decode("utf-8")
        logger.info("TTS Gemini (%s/%s) gerado com sucesso", settings.tts.gemini_model, settings.tts.gemini_voice)
        return audio_b64
    except Exception as e:
        logger.warning("Gemini TTS falhou: %s", e)
        return None


def _try_google_cloud_tts(text: str) -> str | None:
    """Tenta gerar audio via Google Cloud Neural2."""
    try:
        from google.cloud import texttospeech

        tts_client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=settings.tts.google_language,
            name=settings.tts.google_voice,
            ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=settings.tts.google_speaking_rate,
            pitch=0.0,
        )
        tts_response = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice_params,
            audio_config=audio_config,
        )
        audio_b64 = base64.b64encode(tts_response.audio_content).decode("utf-8")
        logger.info("TTS Google Cloud (Neural2) gerado com sucesso (%d chars)", len(text))
        return audio_b64
    except Exception as e:
        logger.warning("Google Cloud TTS falhou: %s", e)
        return None


async def _try_openai_tts(text: str) -> str | None:
    """Tenta gerar audio via OpenAI TTS (async)."""
    try:
        import openai

        client = openai.AsyncOpenAI(api_key=settings.tts.openai_api_key)
        response = await client.audio.speech.create(
            model=settings.tts.model,
            voice=settings.tts.voice,
            input=text,
        )
        audio_b64 = base64.b64encode(response.content).decode("utf-8")
        logger.info("TTS OpenAI (%s) gerado com sucesso (%d chars)", settings.tts.voice, len(text))
        return audio_b64
    except Exception as e:
        logger.warning("OpenAI TTS falhou: %s", e)
        return None
