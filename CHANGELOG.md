# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]
### Added
- Parakeet engine (`engine: parakeet`): NVIDIA Parakeet TDT v3 via parakeet-mlx on Apple Silicon. Roughly 100x realtime after warmup, 25 European languages with automatic detection, word-level timestamps in verbose_json.
- Model Manager lists models for all local engines (faster-whisper, MLX Whisper, Parakeet) with an Engine column; selecting a model also switches the active engine.

### Changed
- Relaxed numpy upper bound to <3 (parakeet-mlx requires numpy >= 2.2.5).

## [0.1.0] - 2026-02-15
### Added
- OpenAI-compatible local API server for external integrations.
- Real-time 200-band FFT spectrum analyzer during recording.
- Splash screen on startup.

### Changed
- Forked from [WhisperWriter](https://github.com/savbell/whisper-writer) and rebranded as Screamscriber.
- Removed close button from status window.
- Multi-monitor window positioning fix.

[0.1.0]: https://github.com/OnePlanDan/screamscriber/releases/tag/v0.1.0
