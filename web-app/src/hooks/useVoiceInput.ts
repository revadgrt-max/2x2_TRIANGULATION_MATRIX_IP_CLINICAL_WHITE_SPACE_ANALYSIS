"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Drives both the Web Speech API (for the actual transcript) and a raw
 * getUserMedia + AnalyserNode tap (purely for a live amplitude reading used
 * to animate the VoiceOrb). The two are independent: if mic-level metering
 * fails to acquire permission for any reason, speech recognition still works
 * and the orb just idles instead of reacting.
 */
const ERROR_MESSAGES: Record<string, string> = {
  "not-allowed": "Microphone access was denied. Allow mic permission in your browser's site settings and try again.",
  "service-not-allowed": "Microphone access was denied. Allow mic permission in your browser's site settings and try again.",
  "no-speech": "No speech detected -- tap the orb and try speaking again.",
  "audio-capture": "No microphone was found. Check that a mic is connected and try again.",
  network: "Voice recognition needs an internet connection to transcribe speech -- check your connection and try again.",
  aborted: "Voice input was cancelled.",
};

export function useVoiceInput(onTranscript: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const [level, setLevel] = useState(0); // smoothed 0..1 amplitude
  const [supported, setSupported] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const recognitionRef = useRef<any>(null); // eslint-disable-line @typescript-eslint/no-explicit-any
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);
  const levelRef = useRef(0);
  const manualStopRef = useRef(false);

  const stop = useCallback((manual = false) => {
    manualStopRef.current = manual;
    setListening(false);
    setLevel(0);
    levelRef.current = 0;
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
    try {
      recognitionRef.current?.stop();
    } catch {
      // already stopped
    }
  }, []);

  useEffect(() => {
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition; // eslint-disable-line @typescript-eslint/no-explicit-any
    if (!SpeechRecognition) {
      setSupported(false);
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    recognition.onresult = (event: any) => { // eslint-disable-line @typescript-eslint/no-explicit-any
      setError(null);
      onTranscript(event.results[0][0].transcript);
    };
    recognition.onend = () => stop();
    recognition.onerror = (event: any) => { // eslint-disable-line @typescript-eslint/no-explicit-any
      if (!manualStopRef.current && event?.error !== "aborted") {
        setError(ERROR_MESSAGES[event?.error] ?? `Voice input error: ${event?.error ?? "unknown"}.`);
      }
      stop();
    };
    recognitionRef.current = recognition;
    return () => {
      recognition.onresult = null;
      recognition.onend = null;
      recognition.onerror = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stop]);

  const tick = useCallback(() => {
    const analyser = analyserRef.current;
    if (analyser) {
      const data = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / data.length);
      levelRef.current = levelRef.current * 0.6 + Math.min(1, rms * 4) * 0.4;
      setLevel(levelRef.current);
    }
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const start = useCallback(async () => {
    if (!recognitionRef.current) return;
    manualStopRef.current = false;
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const Ctx = window.AudioContext || (window as any).webkitAudioContext; // eslint-disable-line @typescript-eslint/no-explicit-any
      const ctx = new Ctx();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;
      rafRef.current = requestAnimationFrame(tick);
    } catch {
      // Metering is best-effort only -- speech recognition still proceeds without it.
    }
    try {
      recognitionRef.current.start();
      setListening(true);
    } catch (e: any) { // eslint-disable-line @typescript-eslint/no-explicit-any
      setError(e?.name === "InvalidStateError" ? "Voice input is already running -- tap the orb again." : "Could not start voice input. Please try again.");
      stop();
    }
  }, [tick, stop]);

  const toggle = useCallback(() => {
    if (listening) stop(true);
    else start();
  }, [listening, start, stop]);

  useEffect(() => () => stop(true), [stop]);

  return { listening, level, supported, error, toggle };
}
