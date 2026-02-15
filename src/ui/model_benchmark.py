import os
import gc
import time
import html
import tempfile
import webbrowser
from datetime import datetime

from PyQt5.QtWidgets import QTextEdit, QPushButton
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor

from ui.base_window import BaseWindow

AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a'}


def get_audio_duration(file_path):
    """Get audio file duration in seconds."""
    try:
        import soundfile as sf
        info = sf.info(file_path)
        return info.duration
    except Exception:
        pass
    try:
        import av
        with av.open(file_path) as container:
            return float(container.duration) / 1_000_000
    except Exception:
        return None


def discover_audio_files(directory):
    """Find all audio files in a directory, sorted by name."""
    if not os.path.isdir(directory):
        return []
    return sorted([
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
    ])


class ModelBenchmarkThread(QThread):
    """Benchmarks downloaded models against test audio samples."""
    log = pyqtSignal(str)
    finished = pyqtSignal(str)  # HTML file path

    def __init__(self, models, audio_files, device, compute_type):
        super().__init__()
        self.models = models
        self.audio_files = audio_files
        self.device = device
        self.compute_type = compute_type
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        from faster_whisper import WhisperModel

        # Pre-compute audio durations
        durations = {}
        for path in self.audio_files:
            name = os.path.basename(path)
            dur = get_audio_duration(path)
            durations[name] = dur
            if dur:
                self.log.emit(f"Found: {name} ({dur:.2f}s)")
            else:
                self.log.emit(f"Found: {name} (duration unknown)")

        self.log.emit(f"\nDevice: {self.device} | Compute: {self.compute_type}")
        self.log.emit(f"Models to test: {len(self.models)} | Samples: {len(self.audio_files)}\n")

        results = {}
        load_times = {}

        for i, model_name in enumerate(self.models):
            if self._cancelled:
                self.log.emit("\nBenchmark cancelled.")
                return

            self.log.emit(f"[{i + 1}/{len(self.models)}] Loading {model_name}...")

            try:
                t0 = time.time()
                model = WhisperModel(
                    model_name, device=self.device, compute_type=self.compute_type
                )
                load_time = time.time() - t0
                load_times[model_name] = load_time
                self.log.emit(f"  Loaded in {load_time:.1f}s")
            except Exception as e:
                self.log.emit(f"  Failed on {self.device} ({e}), retrying on CPU...")
                try:
                    t0 = time.time()
                    model = WhisperModel(
                        model_name, device="cpu", compute_type=self.compute_type
                    )
                    load_time = time.time() - t0
                    load_times[model_name] = load_time
                    self.log.emit(f"  Loaded on CPU in {load_time:.1f}s")
                except Exception as e2:
                    self.log.emit(f"  ERROR: Could not load {model_name}: {e2}")
                    continue

            results[model_name] = {}

            for path in self.audio_files:
                if self._cancelled:
                    break

                name = os.path.basename(path)
                self.log.emit(f"  {name}...")

                try:
                    t0 = time.time()
                    segments, info = model.transcribe(path)
                    text = "".join(seg.text for seg in segments)
                    elapsed = time.time() - t0

                    dur = durations.get(name) or info.duration
                    if dur and not durations.get(name):
                        durations[name] = dur
                    rtf = dur / elapsed if dur and elapsed > 0 else 0

                    results[model_name][name] = {
                        "time": elapsed,
                        "text": text.strip(),
                        "rtf": rtf,
                    }
                    self.log.emit(f"    {elapsed:.2f}s ({rtf:.1f}x realtime)")
                except Exception as e:
                    results[model_name][name] = {
                        "time": 0,
                        "text": f"ERROR: {e}",
                        "rtf": 0,
                    }
                    self.log.emit(f"    ERROR: {e}")

            # Unload model
            self.log.emit(f"  Unloading {model_name}...")
            del model
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

            # Wait 3 seconds before next model
            if i < len(self.models) - 1 and not self._cancelled:
                self.log.emit("  Cooling down (3s)...")
                for _ in range(30):
                    if self._cancelled:
                        break
                    time.sleep(0.1)
                self.log.emit("")

        if self._cancelled:
            self.log.emit("\nBenchmark cancelled.")
            return

        self.log.emit("\nGenerating report...")
        html_content = self._generate_html(results, durations, load_times)

        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.html', delete=False,
            prefix='screamscriber_benchmark_'
        )
        tmp.write(html_content)
        tmp.close()

        self.log.emit(f"Report: {tmp.name}")
        self.finished.emit(tmp.name)

    def _generate_html(self, results, durations, load_times):
        audio_names = [os.path.basename(p) for p in self.audio_files]
        models = list(results.keys())
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        h = []
        h.append('<!DOCTYPE html>')
        h.append('<html><head><meta charset="utf-8">')
        h.append('<title>Screamscriber Benchmark</title>')
        h.append('<style>')
        h.append(
            '* { box-sizing: border-box; }\n'
            'body { font-family: "Segoe UI", system-ui, sans-serif; margin: 0;'
            ' padding: 24px 40px; background: #fafafa; color: #333; }\n'
            'h1 { margin-bottom: 4px; }\n'
            'h2 { color: #444; margin-top: 36px; margin-bottom: 12px; }\n'
            '.meta { color: #888; font-size: 0.9em; margin-bottom: 28px; }\n'
            'table { border-collapse: collapse; width: 100%; margin-bottom: 20px;'
            ' background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.07);'
            ' border-radius: 8px; overflow: hidden; }\n'
            'th { background: #2d2d2d; color: #fff; padding: 12px 14px;'
            ' text-align: left; font-weight: 600; font-size: 0.88em; }\n'
            'th small { font-weight: 400; opacity: 0.65; display: block; }\n'
            'td { padding: 10px 14px; border-bottom: 1px solid #f0f0f0;'
            ' font-size: 0.88em; }\n'
            'tr:last-child td { border-bottom: none; }\n'
            'tr:hover { background: #f8f9fa; }\n'
            '.fast { color: #2e7d32; font-weight: 600; }\n'
            '.medium { color: #e65100; font-weight: 600; }\n'
            '.slow { color: #c62828; font-weight: 600; }\n'
            '.avg-row td { font-weight: 700; background: #f5f5f5;'
            ' border-top: 2px solid #ddd; }\n'
            '.output-cell { max-width: 350px; font-size: 0.84em;'
            ' line-height: 1.5; color: #444; }\n'
            'td[title] { cursor: help; }\n'
            '.duration { color: #999; }\n'
        )
        h.append('</style></head><body>')
        h.append('<h1>Screamscriber Benchmark</h1>')
        h.append(
            f'<p class="meta">{now} &middot; Device: {html.escape(self.device)}'
            f' &middot; Compute: {html.escape(self.compute_type)}'
            f' &middot; {len(models)} models'
            f' &middot; {len(audio_names)} samples</p>'
        )

        # --- Speed table ---
        h.append('<h2>Speed</h2>\n<table>\n<tr><th>Sample</th><th>Duration</th>')
        for m in models:
            lt = load_times.get(m)
            lt_str = f'<small>loaded in {lt:.1f}s</small>' if lt else ''
            h.append(f'<th>{html.escape(m)}{lt_str}</th>')
        h.append('</tr>')

        avg_rtfs = {m: [] for m in models}

        for af in audio_names:
            dur = durations.get(af)
            dur_str = f'{dur:.2f}s' if dur else '?'
            h.append(
                f'<tr><td>{html.escape(af)}</td>'
                f'<td class="duration">{dur_str}</td>'
            )
            for m in models:
                r = results.get(m, {}).get(af)
                if r and r['time'] > 0:
                    rtf = r['rtf']
                    avg_rtfs[m].append(rtf)
                    css = 'fast' if rtf >= 15 else ('medium' if rtf >= 5 else 'slow')
                    tip = html.escape(r['text'])
                    h.append(
                        f'<td class="{css}" title="{tip}">'
                        f'{r["time"]:.2f}s ({rtf:.1f}x)</td>'
                    )
                else:
                    h.append('<td>N/A</td>')
            h.append('</tr>')

        # Average row
        h.append('<tr class="avg-row"><td>Average</td><td></td>')
        for m in models:
            vals = avg_rtfs[m]
            if vals:
                avg = sum(vals) / len(vals)
                h.append(f'<td>{avg:.1f}x</td>')
            else:
                h.append('<td>N/A</td>')
        h.append('</tr>\n</table>')

        # --- Output table ---
        h.append('<h2>Output</h2>\n<table>\n<tr><th>Sample</th>')
        for m in models:
            h.append(f'<th>{html.escape(m)}</th>')
        h.append('</tr>')

        for af in audio_names:
            h.append(f'<tr><td>{html.escape(af)}</td>')
            for m in models:
                r = results.get(m, {}).get(af)
                text = html.escape(r['text']) if r else 'N/A'
                h.append(f'<td class="output-cell">{text}</td>')
            h.append('</tr>')

        h.append('</table>\n</body>\n</html>')
        return '\n'.join(h)


class BenchmarkProgressWindow(BaseWindow):
    """Popup window showing live benchmark progress."""

    def __init__(self):
        super().__init__('Model Benchmark', 600, 450)
        self.benchmark_thread = None
        self._init_progress_ui()

    def _init_progress_ui(self):
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        font = QFont("Monospace", 8)
        font.setStyleHint(QFont.Monospace)
        self.log_area.setFont(font)
        self.log_area.setStyleSheet(
            "background: #1e1e1e; color: #d4d4d4; border: none; "
            "border-radius: 8px; padding: 8px;"
        )
        self.main_layout.addWidget(self.log_area)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.main_layout.addWidget(self.cancel_btn)

    def append_log(self, text):
        self.log_area.append(text)
        self.log_area.moveCursor(QTextCursor.End)

    def _on_cancel(self):
        if self.benchmark_thread and self.benchmark_thread.isRunning():
            self.benchmark_thread.cancel()
            self.cancel_btn.setText("Cancelling...")
            self.cancel_btn.setEnabled(False)
        else:
            self.close()

    def _on_finished(self, html_path):
        self.append_log("\nDone! Opening report in browser...")
        self.cancel_btn.setText("Close")
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self.close)
        webbrowser.open(f"file://{html_path}")

    def start_benchmark(self, models, audio_files, device, compute_type):
        self.benchmark_thread = ModelBenchmarkThread(
            models, audio_files, device, compute_type
        )
        self.benchmark_thread.log.connect(self.append_log)
        self.benchmark_thread.finished.connect(self._on_finished)
        self.benchmark_thread.start()
        self.show()

    def closeEvent(self, event):
        if self.benchmark_thread and self.benchmark_thread.isRunning():
            self.benchmark_thread.cancel()
            self.benchmark_thread.wait(5000)
        super().closeEvent(event)
