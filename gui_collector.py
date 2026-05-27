import atexit
import os
import signal
import sys
from pathlib import Path

# rag_core für Bulk-Download-Module
_BASE = Path(os.path.dirname(os.path.abspath(__file__)))
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# Speicherpfad laden bevor rag_core geladen wird
try:
    import json
    _state_file = _BASE / "collector_state.json"
    if _state_file.exists():
        with open(_state_file, "r", encoding="utf-8") as _f:
            _saved = json.load(_f)
            if isinstance(_saved, dict):
                _saved_dir = _saved.get("data_dir")
                if _saved_dir:
                    os.environ["RAG_DATA_DIR"] = _saved_dir
except Exception:
    pass

from rag_core.gui_resources import (
    NUM_THREADS,
    child_env,
    low_priority_cmd,
    resource_summary,
    MAX_WIKI_DOWNLOADS,
    wait_for_ram,
)
from rag_core.gui_task_pool import BackgroundTaskPool
from rag_core.collector_plan import (
    QUALITY_PIPELINE_PHASES,
    STACKEXCHANGE_QUALITY_ORDER,
    WIKIMEDIA_DUMP_LEGACY_FULL,
    WIKIMEDIA_DUMP_OPTIONAL,
    WIKI_MAX_DONE,
    WIKI_MAX_QUEUE,
    SO_MAX_PAGES_PER_TAG,
    get_wiki_link_keywords,
    get_wiki_blocked_patterns,
)
from rag_core.bulk_sources import STACKEXCHANGE_DUMPS, STACKEXCHANGE_UNIVERSAL, GIT_SOURCES_UNIVERSAL
from rag_core.knowledge_stats import gather_stats, format_stats_markup

_env = child_env()
for _k, _v in _env.items():
    if _k.startswith(("OMP_", "MKL_", "NUMEXPR_", "OPENBLAS_", "VECLIB_", "TOKENIZERS")):
        os.environ[_k] = _v

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango
import threading
import time
import json
import requests
import subprocess
import wikipediaapi

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = BASE_DIR / "data"
STATE_FILE = BASE_DIR / "collector_state.json"
PID_FILE = BASE_DIR / ".collector.pid"


def _acquire_pid_lock() -> bool:
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            PID_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    return True


def _release_pid_lock():
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

class CollectorState:
    def __init__(self):
        try:
            from rag_core.quality import get_current_domains
            from rag_core.domains import DOMAIN_IT
            domains = get_current_domains()
            seeds = []
            seen = set()
            for d in domains:
                for t in d.wiki_seed_topics:
                    if t not in seen:
                        seen.add(t)
                        seeds.append(t)
            if not seeds:
                seeds = list(DOMAIN_IT.wiki_seed_topics)
            self.wiki_topics = seeds
        except Exception:
            self.wiki_topics = [
                "Informatik", "Softwarearchitektur", "Python_(Programmiersprache)",
                "Linux", "Künstliche_Intelligenz", "Netzwerkprotokoll",
                "Maschinelles_Lernen", "Betriebssystem", "Datenbank", "Kryptographie",
            ]
        self.so_tags = [
            "python", "javascript", "linux", "bash", "docker", "reactjs", "c++",
            "java", "sql", "git", "php", "c#", "ruby", "rust", "swift", "go",
            "kotlin", "typescript", "kubernetes", "aws", "terraform", "ansible",
            "nginx", "mongodb", "postgresql", "redis", "elasticsearch", "graphql",
            "rest-api", "microservices", "security", "devops", "machine-learning",
            "tensorflow", "pytorch", "pandas", "numpy", "django", "flask",
            "spring-boot", "node.js", "express", "vue.js", "angular", "tailwind-css",
            "security", "authentication", "authorization", "encryption", "owasp",
            "xss", "sql-injection", "csrf", "jwt", "oauth-2.0", "penetration-testing",
        ]
        self.man_sections = ["1", "2", "3", "4", "5", "6", "7", "8"]
        
        self.data = {
            "wiki_queue": [],
            "wiki_done": [],
            "so_progress": {tag: 1 for tag in self.so_tags},
            "man_done": [],
            "mdn_done": False,
            "so_key": "",
            "processed_dumps": [],
            "processed_se": [],
            "data_dir": "",
        }
        self.load()
        
        if not self.data["wiki_queue"]:
            self.data["wiki_queue"] = self.wiki_topics.copy()

    def load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        self.data.update(saved)
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.data, f)

class RAGCollectorGUI(Gtk.Window):
    def __init__(self):
        super().__init__(title="RAG Wissenskollektor")
        self.set_default_size(680, 560)
        self.set_border_width(15)
        self.set_position(Gtk.WindowPosition.CENTER)
        
        self.state = CollectorState()
        saved_dir = self.state.data.get("data_dir")
        if saved_dir:
            global DATA_DIR
            DATA_DIR = Path(saved_dir)
            os.environ["RAG_DATA_DIR"] = str(saved_dir)
            import rag_core.config
            rag_core.config.DATA_DIR = DATA_DIR

        self.running = False
        self.paused = False
        self.thread = None
        self.ingest_queue = []
        self.ingest_running = False
        self._lock = threading.Lock()
        self._child_procs: list = []
        self._disk_busy = False
        self._knowledge_busy = False
        self._data_bytes_hint = 0
        self._task_pool = BackgroundTaskPool(self.log, lambda: self.running)

        self.connect("delete-event", self.on_window_delete)
        
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(vbox)
        
        header = Gtk.Label()
        try:
            from rag_core.quality import get_current_domains
            domain_names = ", ".join(d.name for d in get_current_domains())
        except Exception:
            domain_names = "IT"
        header.set_markup(
            f"<big><b>📚 Wissensdatenbank ({GLib.markup_escape_text(domain_names)})</b></big>\n"
            "<small>Prime-Index: Kuratierte Quellen, domänengefiltertes Wikipedia – ohne Rauschen</small>\n"
            f"<small>{GLib.markup_escape_text(resource_summary())}</small>"
        )
        header.set_justify(Gtk.Justification.CENTER)
        vbox.pack_start(header, False, False, 5)
        
        self.status_label = Gtk.Label(label="Status: Bereit (Wartet auf Start)")
        self.status_label.set_halign(Gtk.Align.START)
        vbox.pack_start(self.status_label, False, False, 0)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        
        self.textview.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.05, 0.05, 0.05, 1))
        self.textview.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.9, 0.9, 0.9, 1))
        font_desc = Pango.FontDescription("Monospace 10")
        self.textview.override_font(font_desc)
        
        scrolled.add(self.textview)
        vbox.pack_start(scrolled, True, True, 0)
        
        # ─── Wissensaufbau & NVMe-Kompilierung Workflow ───
        workflow_frame = Gtk.Frame(label=" 🛠️ Schritt-für-Schritt KI-Wissen & NVMe-Format ")
        workflow_frame.set_margin_top(6)
        vbox.pack_start(workflow_frame, False, False, 0)

        workflow_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        workflow_box.set_margin_start(12)
        workflow_box.set_margin_end(12)
        workflow_box.set_margin_top(10)
        workflow_box.set_margin_bottom(10)
        workflow_frame.add(workflow_box)

        # 1-Klick Komplett-Prozess
        one_click_title = Gtk.Label()
        one_click_title.set_markup("<span size='medium' weight='bold' color='#60a5fa'>🔥 Empfohlener 1-Klick-Weg:</span>")
        one_click_title.set_halign(Gtk.Align.START)
        workflow_box.pack_start(one_click_title, False, False, 0)

        self.btn_one_click = Gtk.Button(label="🔥 1-Klick Komplett-Durchlauf (Sammeln & NVMe-Format kompilieren)")
        self.btn_one_click.get_style_context().add_class("suggested-action")
        self.btn_one_click.connect("clicked", self.on_one_click_clicked)
        self.btn_one_click.set_tooltip_text(
            "Führt Schritt 1 (Sammeln/Indexieren) und Schritt 2 (Kompilieren ins schnelle NVMe-Format) vollautomatisch hintereinander aus."
        )
        workflow_box.pack_start(self.btn_one_click, False, False, 2)

        # Trenner
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(6)
        sep.set_margin_bottom(6)
        workflow_box.pack_start(sep, False, False, 0)

        # Manuelle Schritte
        step_title = Gtk.Label()
        step_title.set_markup("<span size='medium' weight='bold'>Alternativ: Manueller Schritt-für-Schritt-Weg</span>")
        step_title.set_halign(Gtk.Align.START)
        workflow_box.pack_start(step_title, False, False, 0)

        # Schritt 1
        step1_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        workflow_box.pack_start(step1_box, False, False, 2)

        step1_label = Gtk.Label()
        step1_label.set_markup("<span weight='bold' color='#3b82f6'>[ Schritt 1 ]</span>")
        step1_label.set_width_chars(12)
        step1_label.set_halign(Gtk.Align.START)
        step1_box.pack_start(step1_label, False, False, 0)

        self.btn_build = Gtk.Button(label="🚀 1. Wissen sammeln & indexieren")
        self.btn_build.connect("clicked", self.on_build_clicked)
        self.btn_build.set_tooltip_text("Rohdaten aus allen Quellen sammeln und in LanceDB-Index laden")
        step1_box.pack_start(self.btn_build, True, True, 0)

        self.btn_pause = Gtk.Button(label="⏸ Pausieren")
        self.btn_pause.connect("clicked", self.on_pause_clicked)
        self.btn_pause.set_sensitive(False)
        step1_box.pack_start(self.btn_pause, False, False, 0)

        self.btn_resume = Gtk.Button(label="▶ Weiter")
        self.btn_resume.connect("clicked", self.on_resume_clicked)
        self.btn_resume.set_sensitive(False)
        step1_box.pack_start(self.btn_resume, False, False, 0)

        # Schritt 2
        step2_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        workflow_box.pack_start(step2_box, False, False, 2)

        step2_label = Gtk.Label()
        step2_label.set_markup("<span weight='bold' color='#ef4444'>[ Schritt 2 ]</span>")
        step2_label.set_width_chars(12)
        step2_label.set_halign(Gtk.Align.START)
        step2_box.pack_start(step2_label, False, False, 0)

        self.nvme_table_combo = Gtk.ComboBoxText()
        self.nvme_table_combo.append_text("it_prime")
        self.nvme_table_combo.append_text("it_knowledge")
        self.nvme_table_combo.set_active(0)
        self.nvme_table_combo.set_tooltip_text("Quelle: LanceDB-Tabelle")
        step2_box.pack_start(self.nvme_table_combo, False, False, 0)

        self.btn_nvme_compile = Gtk.Button(label="⚡ 2. In NVMe-Format kompilieren")
        self.btn_nvme_compile.connect("clicked", self.on_nvme_compile_clicked)
        self.btn_nvme_compile.get_style_context().add_class("destructive-action")
        self.btn_nvme_compile.set_tooltip_text("LanceDB-Index in das extrem schnelle, komprimierte NVMe-Format konvertieren")
        step2_box.pack_start(self.btn_nvme_compile, True, True, 0)

        self.btn_nvme_stop = Gtk.Button(label="⏹ Stop")
        self.btn_nvme_stop.connect("clicked", self.on_nvme_stop_clicked)
        self.btn_nvme_stop.set_sensitive(False)
        step2_box.pack_start(self.btn_nvme_stop, False, False, 0)

        # Optionen & Fortschritt
        options_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        options_box.set_margin_top(4)
        workflow_box.pack_start(options_box, False, False, 0)

        self.chk_auto_nvme = Gtk.CheckButton(label="Nach Schritt 1 automatisch Schritt 2 (Kompilieren) ausführen")
        self.chk_auto_nvme.set_active(True)
        self.chk_auto_nvme.set_tooltip_text("Startet nach der Indexierung direkt die Konvertierung ins NVMe-Format.")
        options_box.pack_start(self.chk_auto_nvme, False, False, 0)

        self.chk_cleanup = Gtk.CheckButton(label="Nach erfolgreicher NVMe-Kompilierung Rohdaten & LanceDB-Index löschen (Speicherplatz freigeben)")
        self.chk_cleanup.set_active(True)
        self.chk_cleanup.set_tooltip_text("Löscht temporäre Downloads in data/ und LanceDB-Tabellen, da diese nun im NVMe-Format sind.")
        options_box.pack_start(self.chk_cleanup, False, False, 0)

        self.nvme_status = Gtk.Label()
        self.nvme_status.set_halign(Gtk.Align.START)
        self.nvme_status.set_line_wrap(True)
        self._update_nvme_status()
        workflow_box.pack_start(self.nvme_status, False, False, 2)

        self.nvme_progress = Gtk.ProgressBar()
        self.nvme_progress.set_show_text(True)
        self.nvme_progress.set_text("Bereit")
        workflow_box.pack_start(self.nvme_progress, False, False, 0)

        self._nvme_thread = None
        self._nvme_stop_event = threading.Event()
        self.pipeline_success = False

        GLib.timeout_add_seconds(120, self._update_nvme_status)

        # Wissensgröße (Index + Rohdaten)
        stats_frame = Gtk.Frame(label=" Wissensbasis ")
        stats_frame.set_margin_top(4)
        vbox.pack_start(stats_frame, False, False, 0)
        self.knowledge_label = Gtk.Label(label="Wissen: wird berechnet…")
        self.knowledge_label.set_halign(Gtk.Align.START)
        self.knowledge_label.set_line_wrap(True)
        self.knowledge_label.set_max_width_chars(72)
        self.knowledge_label.set_margin_left(8)
        self.knowledge_label.set_margin_right(8)
        self.knowledge_label.set_margin_top(6)
        self.knowledge_label.set_margin_bottom(6)
        stats_frame.add(self.knowledge_label)
        GLib.timeout_add_seconds(120, self.update_knowledge_stats)
        self.update_knowledge_stats()

        # Speicherort
        path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        vbox.pack_start(path_box, False, False, 0)

        path_label = Gtk.Label(label="Speicherort:")
        path_box.pack_start(path_label, False, False, 0)

        self.btn_choose_dir = Gtk.FileChooserButton(title="Speicherort für RAG-Daten wählen", action=Gtk.FileChooserAction.SELECT_FOLDER)
        current_data_dir = self.state.data.get("data_dir", str(BASE_DIR / "data"))
        self.btn_choose_dir.set_filename(current_data_dir)
        self.btn_choose_dir.connect("current-folder-changed", self.on_data_dir_changed)
        path_box.pack_start(self.btn_choose_dir, True, True, 0)

        # Disk Space + API Key
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        vbox.pack_start(info_box, False, False, 0)

        self.disk_label = Gtk.Label(label="Speicher: --")
        info_box.pack_start(self.disk_label, False, False, 0)
        GLib.timeout_add_seconds(60, self.update_disk_space)
        self.update_disk_space()

        key_label = Gtk.Label(label="SO API Key:")
        info_box.pack_start(key_label, False, False, 0)
        self.entry_key = Gtk.Entry()
        self.entry_key.set_placeholder_text("StackExchange API Key (optional)...")
        self.entry_key.set_text(self.state.data.get("so_key", ""))
        self.entry_key.connect("changed", self.on_key_changed)
        info_box.pack_start(self.entry_key, True, True, 0)

        for d in [
            "rfcs", "wikipedia", "stackoverflow", "dumps", "stackexchange",
            "arch-wiki", "official-docs", "owasp", "mdn-web-docs", "tldr-pages",
            "manpages", "linux-docs", "gutenberg", "books", "textbooks",
        ]:
            (DATA_DIR / d).mkdir(parents=True, exist_ok=True)
            
    def on_data_dir_changed(self, widget):
        new_path = widget.get_filename()
        if new_path:
            global DATA_DIR
            DATA_DIR = Path(new_path)
            os.environ["RAG_DATA_DIR"] = str(new_path)
            
            import rag_core.config
            rag_core.config.DATA_DIR = DATA_DIR
            
            self.state.data["data_dir"] = str(new_path)
            self.state.save()
            
            for d in [
                "rfcs", "wikipedia", "stackoverflow", "dumps", "stackexchange",
                "arch-wiki", "official-docs", "owasp", "mdn-web-docs", "tldr-pages",
                "manpages", "linux-docs", "gutenberg", "books", "textbooks",
            ]:
                (DATA_DIR / d).mkdir(parents=True, exist_ok=True)
                
            self.log(f"📁 Speicherort geändert zu: {new_path}")
            self.update_disk_space()
            self.update_knowledge_stats()

    def log(self, message):
        GLib.idle_add(self._log_gui, str(message))

    def _log_gui(self, message):
        try:
            buf = self.textview.get_buffer()
            if buf.get_char_count() > 250_000:
                trim = buf.get_iter_at_offset(buf.get_char_count() - 180_000)
                buf.delete(buf.get_start_iter(), trim)
            end_iter = buf.get_end_iter()
            buf.insert(end_iter, message + "\n")
            mark = buf.create_mark(None, buf.get_end_iter(), False)
            self.textview.scroll_to_mark(mark, 0.05, True, 0.0, 1.0)
            safe = GLib.markup_escape_text(message[:500])
            self.status_label.set_markup(f"<b>Status:</b> {safe}")
        except Exception:
            pass

    def on_window_delete(self, widget, event):
        self.log("🛡️ Sauberer Shutdown…")
        with self._lock:
            self.running = False
            self.paused = False
        try:
            self._task_pool.stop()
        except Exception:
            pass
        self._terminate_children()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        try:
            self.state.save()
        except Exception:
            pass
        _release_pid_lock()
        return False

    def _terminate_children(self):
        with self._lock:
            procs = list(self._child_procs)
            self._child_procs.clear()
        for p in procs:
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        deadline = time.time() + 3
        for p in procs:
            try:
                while p.poll() is None and time.time() < deadline:
                    time.sleep(0.1)
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass

    def _spawn(self, cmd, **kwargs):
        kwargs.setdefault("env", child_env())
        try:
            proc = subprocess.Popen(low_priority_cmd(cmd), **kwargs)
        except FileNotFoundError:
            proc = subprocess.Popen(cmd, **kwargs)
        with self._lock:
            self._child_procs.append(proc)
        return proc

    def _release_proc(self, proc):
        with self._lock:
            try:
                self._child_procs.remove(proc)
            except ValueError:
                pass

    def _set_busy_ui(self, busy: bool):
        self.btn_build.set_sensitive(not busy)
        self.btn_one_click.set_sensitive(not busy)
        self.chk_auto_nvme.set_sensitive(not busy)
        self.chk_cleanup.set_sensitive(not busy)
        if not busy:
            self.btn_pause.set_sensitive(False)
            self.btn_resume.set_sensitive(False)
        self.running = busy

    def set_busy(self, busy: bool):
        GLib.idle_add(self._set_busy_ui, busy)
        
    def on_start_clicked(self, widget):
        self.on_build_clicked(widget)

    def on_pause_clicked(self, widget):
        self.paused = True
        self.btn_resume.set_sensitive(True)
        self.btn_pause.set_sensitive(False)
        self.log("⏸ Pausiert. Warte auf Abschluss des aktuellen Vorgangs...")

    def on_resume_clicked(self, widget):
        self.paused = False
        self.btn_resume.set_sensitive(False)
        self.btn_pause.set_sensitive(True)
        self.log("▶ Sammeln fortgesetzt...")

    def update_disk_space(self):
        if self._disk_busy:
            return True
        self._disk_busy = True
        threading.Thread(target=self._disk_space_worker, daemon=True).start()
        return True

    def _disk_space_worker(self):
        free_gb, used_gb = 0.0, 0.0
        try:
            stat = os.statvfs(DATA_DIR)
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
            r = subprocess.run(
                ["du", "-sb", str(DATA_DIR)],
                capture_output=True,
                text=True,
                timeout=90,
                env=child_env(),
            )
            if r.returncode == 0 and r.stdout.strip():
                used_b = int(r.stdout.split()[0])
                used_gb = used_b / (1024**3)
                self._data_bytes_hint = used_b
        except Exception:
            pass
        finally:
            self._disk_busy = False
        GLib.idle_add(self._apply_disk_label, free_gb, used_gb)
        GLib.idle_add(self._trigger_knowledge_refresh)

    def _apply_disk_label(self, free_gb: float, used_gb: float):
        try:
            self.disk_label.set_markup(
                f"<b>Gesammelt:</b> {used_gb:.2f} GB | <b>Frei:</b> {free_gb:.1f} GB"
            )
        except Exception:
            pass
        return False

    def _trigger_knowledge_refresh(self):
        self.update_knowledge_stats()
        return False

    def update_knowledge_stats(self):
        if self._knowledge_busy:
            return True
        self._knowledge_busy = True
        threading.Thread(target=self._knowledge_stats_worker, daemon=True).start()
        return True

    def _knowledge_stats_worker(self):
        markup = "<i>Wissen: nicht verfügbar</i>"
        try:
            stats = gather_stats(
                use_du_for_data=False,
                data_bytes_hint=self._data_bytes_hint or None,
            )
            markup = format_stats_markup(stats)
        except Exception:
            pass
        finally:
            self._knowledge_busy = False
        GLib.idle_add(self._apply_knowledge_label, markup)

    def _apply_knowledge_label(self, markup: str):
        try:
            self.knowledge_label.set_markup(markup)
        except Exception:
            pass
        return False

    def on_one_click_clicked(self, widget):
        self.chk_auto_nvme.set_active(True)
        self.log("🔥 1-Klick Komplett-Durchlauf gestartet (Schritt 1 + Schritt 2)...")
        self.on_build_clicked(widget)

    def on_build_clicked(self, widget):
        if self.running:
            self.log("⚠️ Bereits aktiv – bitte warten.")
            return
        self.running = True
        self.paused = False
        self.pipeline_success = False
        GLib.idle_add(self._set_busy_ui, True)
        self.btn_pause.set_sensitive(True)
        self.btn_resume.set_sensitive(False)
        self.thread = threading.Thread(target=self.run_quality_pipeline, daemon=True)
        self.thread.start()

    def on_quality_clicked(self, widget):
        self.on_build_clicked(widget)

    def on_index_clicked(self, widget):
        self.on_build_clicked(widget)

    def run_quality_pipeline(self):
        """Maximale Wissensdatenbank: Alle verfügbaren Quellen."""
        self.log("🎯 MAXIMALE WISSENSDATENBANK – Alle Quellen")
        phases = [
            "1. Kern-Dokumentation (MDN, TLDR, Git-Docs, OWASP, Arch Wiki)",
            "2. Referenz (RFCs, Manpages, Linux-Docs)",
            "3. Stack Exchange (qualitätsgefiltert)",
            "4. Wikipedia API (domänenspezifisch)",
            "5. Stack Overflow API (Top-Tags)",
            "6. Project Gutenberg (Bücher & Literatur)",
            "7. Wikipedia Vollarchive (Dumps)",
            "8. Deine Projekte + Prime-Index",
        ]
        for phase in phases:
            self.log(f"   {phase}")
        self.log("")

        self._task_pool.start()

        # Phase 1+2: Dokumentation & Referenz (vollständig sequentiell für minimalen RAM/Festplatten-Footprint)
        for name, fn in [
            ("MDN", self.collect_mdn_docs),
            ("TLDR", self.collect_tldr_pages),
            ("Git+OWASP+Arch", self.collect_all_git_docs),
            ("OpenStax", self.collect_universal_git_docs),
            ("RFCs", self.collect_rfcs),
            ("Manpages", self.collect_manpages),
            ("Linux-Docs", self.download_linux_docs),
        ]:
            if not self.running:
                break
            self.check_pause()
            self.log(f"\n⚡ Starte Download-Phase: {name}…")
            try:
                fn()
            except Exception as e:
                self.log(f"⚠️ Fehler in Phase {name}: {e}")
            import gc; gc.collect()

        import gc; gc.collect()
        if not self.running:
            GLib.idle_add(self._finish_work)
            return

        # Phase 3: Stack Exchange (IT + Universal)
        wait_for_ram(floor_mb=1000, log=self.log)
        self.collect_stackexchange_quality()
        if not self.running:
            GLib.idle_add(self._finish_work)
            return

        wait_for_ram(floor_mb=800, log=self.log)
        self.log("\n🌍 Phase 3b: Stack Exchange Universal (Physik, Mathe, Bio, Recht…)…")
        self.collect_stackexchange_universal()
        if not self.running:
            GLib.idle_add(self._finish_work)
            return

        # Phase 4–5: API-Sammlung
        wait_for_ram(floor_mb=800, log=self.log)
        self.log("\n📡 Phase 4: Wikipedia API (domänenbasiert)…")
        self.collect_wikipedia()
        if not self.running:
            GLib.idle_add(self._finish_work)
            return

        wait_for_ram(floor_mb=800, log=self.log)
        self.log("\n📡 Phase 5: Stack Overflow API (Top-Tags)…")
        self.collect_stackoverflow()
        if not self.running:
            GLib.idle_add(self._finish_work)
            return

        # Phase 6: Bücher & Literatur
        wait_for_ram(floor_mb=800, log=self.log)
        self.log("\n📚 Phase 6: Project Gutenberg + Fachbücher…")
        self._task_pool.start()
        self._task_pool.submit(self.download_gutenberg, "Gutenberg")
        self._task_pool.submit(self.download_archive_org_books, "Archive.org")
        self._task_pool.wait()
        if not self.running:
            GLib.idle_add(self._finish_work)
            return

        # Phase 7: Wikipedia Vollarchive
        wait_for_ram(floor_mb=1000, log=self.log)
        self.log("\n📦 Phase 7: Wikipedia Dumps (Allgemeinwissen-Vortraining)…")
        self.download_dumps()
        if not self.running:
            GLib.idle_add(self._finish_work)
            return

        # Phase 8: Prime-Index + eigene Projekte
        wait_for_ram(floor_mb=1200, log=self.log)
        self.run_prime_indexing(fresh=True)

    def run_prime_indexing(self, fresh: bool = False):
        """Nur it_prime – kuratierte Quellen."""
        venv_python = BASE_DIR / ".venv" / "bin" / "python"
        scripts = BASE_DIR / "scripts"

        self.log("\n🚀 Prime-Index (it_prime) – nur hochwertige IT-Quellen…")
        if fresh:
            self.log("   Modus: Neuaufbau (--fresh)")

        # Stack Exchange XML → it_prime (vor build_prime, damit SE in DB ist)
        se_script = scripts / "ingest_stackexchange.py"
        if se_script.exists() and (DATA_DIR / "stackexchange").exists():
            try:
                self.log("📚 Stack Exchange → it_prime …")
                proc = self._spawn(
                    [str(venv_python), str(se_script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout:
                    if line.strip() and self.running:
                        self.log(f"  {line.strip()[:120]}")
                proc.wait()
                self._release_proc(proc)
            except Exception as e:
                self.log(f"⚠️ SE-Ingest: {e}")

        if not self.running:
            GLib.idle_add(self._finish_work)
            return

        index_script = scripts / "build_prime_index.py"
        if not index_script.exists():
            self.log("❌ build_prime_index.py fehlt")
            GLib.idle_add(self._finish_work)
            return

        cmd = [str(venv_python), str(index_script)]
        if fresh:
            cmd.append("--fresh")
        try:
            proc = self._spawn(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(scripts),
            )
            for line in proc.stdout:
                if not self.running:
                    proc.terminate()
                    break
                if line.strip():
                    self.log(line.strip())
            proc.wait()
            self._release_proc(proc)
            if proc.returncode == 0:
                self.log("✅ it_prime bereit – im Hauptmenü „Assistent“ öffnen")
                self.pipeline_success = True
            else:
                self.log(f"⚠️ Exit-Code {proc.returncode}")
        except Exception as e:
            self.log(f"❌ Index: {e}")
        finally:
            GLib.idle_add(self._finish_work)

    def run_indexing(self):
        """Legacy-Alias → Prime-Index."""
        self.run_prime_indexing(fresh=False)

    def _finish_work(self):
        self.btn_pause.set_sensitive(False)
        self.btn_resume.set_sensitive(False)
        self.btn_build.set_sensitive(True)
        self.btn_one_click.set_sensitive(True)
        self.chk_auto_nvme.set_sensitive(True)
        self.running = False
        self.update_knowledge_stats()

        if getattr(self, "pipeline_success", False) and self.chk_auto_nvme.get_active():
            self.pipeline_success = False
            self.log("\n⚡ Schritt 1 erfolgreich abgeschlossen. Starte automatische NVMe-Kompilierung...")
            GLib.idle_add(self.on_nvme_compile_clicked, None)

    def cleanup_intermediate_data(self, table_name: str):
        self.log("\n🧹 Starte automatische Speicherbereinigung...")
        import shutil
        
        # 1. Temporäre Rohdaten löschen
        cleaned_dirs = 0
        for d in [
            "rfcs", "wikipedia", "stackoverflow", "dumps", "stackexchange",
            "arch-wiki", "official-docs", "owasp", "mdn-web-docs", "tldr-pages",
            "manpages", "linux-docs", "gutenberg", "books", "textbooks"
        ]:
            dir_path = DATA_DIR / d
            if dir_path.exists():
                try:
                    for item in dir_path.iterdir():
                        if item.is_file():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item)
                    cleaned_dirs += 1
                except Exception as e:
                    self.log(f"⚠️ Fehler beim Bereinigen von data/{d}: {e}")
        
        # 2. LanceDB Quell-Tabelle löschen
        db_dir = BASE_DIR / "lancedb_index" / f"{table_name}.lance"
        if db_dir.exists():
            try:
                shutil.rmtree(db_dir)
                self.log(f"🗑️ LanceDB-Index gelöscht: {table_name}.lance")
            except Exception as e:
                self.log(f"⚠️ Fehler beim Löschen der LanceDB-Tabelle: {e}")
                
        self.log(f"✅ Bereinigung abgeschlossen. {cleaned_dirs} Datenverzeichnisse gesäubert.")
        GLib.idle_add(self.update_disk_space)
        GLib.idle_add(self.update_knowledge_stats)

    # ─── NVMe Machine Core Handlers ───

    def _update_nvme_status(self):
        try:
            from rag_core.config import NVME_KNOWLEDGE_PATH
            store_path = NVME_KNOWLEDGE_PATH
            if store_path.exists():
                size_mb = store_path.stat().st_size / (1024 * 1024)
                from rag_core.nvme_blocks import NVMeBlockStore
                store = NVMeBlockStore(store_path, readonly=True)
                s = store.stats()
                store.close()
                markup = (
                    f"<b>NVMe-Store:</b> {size_mb:.1f} MB | "
                    f"<b>{s['total_blocks']:,}</b> Blöcke "
                    f"({s['by_type'].get('articles', 0)} Artikel, "
                    f"{s['by_type'].get('facts', 0)} Fakten, "
                    f"{s['by_type'].get('events', 0)} Ereignisse) | "
                    f"{s['years_indexed']} Jahre"
                )
            else:
                markup = "<i>NVMe-Store: noch nicht erstellt – Kompilierung starten</i>"
            GLib.idle_add(self.nvme_status.set_markup, markup)
        except Exception as e:
            GLib.idle_add(self.nvme_status.set_markup, f"<i>NVMe-Status: {e}</i>")
        return True

    def on_nvme_compile_clicked(self, widget):
        if self._nvme_thread and self._nvme_thread.is_alive():
            self.log("⚠️ NVMe-Kompilierung läuft bereits")
            return

        table_name = self.nvme_table_combo.get_active_text()
        self.log(f"⚡ NVMe-Kompilierung gestartet (Quelle: {table_name})")

        self._nvme_stop_event.clear()
        self.btn_nvme_compile.set_sensitive(False)
        self.btn_nvme_stop.set_sensitive(True)
        self.nvme_progress.set_fraction(0.0)
        self.nvme_progress.set_text("Starte…")

        self._nvme_thread = threading.Thread(
            target=self._nvme_compile_worker,
            args=(table_name,),
            daemon=True,
        )
        self._nvme_thread.start()

    def on_nvme_stop_clicked(self, widget):
        self.log("⏹ NVMe-Kompilierung wird gestoppt…")
        self._nvme_stop_event.set()
        self.btn_nvme_stop.set_sensitive(False)

    def _nvme_compile_worker(self, table_name: str):
        import traceback as tb

        try:
            from rag_core.nvme_blocks import NVMeBlockStore
            from rag_core.knowledge_compiler import migrate_lancedb_to_nvme
        except ImportError as e:
            self.log(f"❌ Import-Fehler: {e}")
            self.log(f"   Stelle sicher, dass nvme_blocks.py und knowledge_compiler.py existieren")
            self.log(f"   Traceback: {tb.format_exc()}")
            GLib.idle_add(self.nvme_progress.set_text, f"Import-Fehler: {e}")
            GLib.idle_add(self.btn_nvme_compile.set_sensitive, True)
            GLib.idle_add(self.btn_nvme_stop.set_sensitive, False)
            return

        store = None
        try:
            from rag_core.config import NVME_KNOWLEDGE_PATH
            store_path = NVME_KNOWLEDGE_PATH
            self.log(f"📂 Store-Pfad: {store_path}")
            store = NVMeBlockStore(store_path)
            self.log(f"   Store geöffnet: {store.index.total_blocks:,} bestehende Blöcke")
        except Exception as e:
            self.log(f"❌ Store konnte nicht geöffnet werden: {e}")
            self.log(f"   Pfad: {store_path}")
            self.log(f"   Traceback:\n{tb.format_exc()}")
            GLib.idle_add(self.nvme_progress.set_text, f"Store-Fehler: {str(e)[:50]}")
            GLib.idle_add(self.btn_nvme_compile.set_sensitive, True)
            GLib.idle_add(self.btn_nvme_stop.set_sensitive, False)
            return

        try:
            def log_fn(msg):
                self.log(msg)
                if "%" in msg:
                    import re
                    m = re.search(r"(\d+\.\d+)%", msg)
                    if m:
                        pct = float(m.group(1))
                        GLib.idle_add(self.nvme_progress.set_fraction, pct / 100.0)
                        GLib.idle_add(self.nvme_progress.set_text, f"{pct:.1f}%")
                if "FEHLER" in msg or "Error" in msg:
                    GLib.idle_add(self.nvme_progress.set_text, msg[:60])

            result = migrate_lancedb_to_nvme(
                store,
                table_name=table_name,
                batch_size=1000000,
                log_fn=log_fn,
                stop_event=self._nvme_stop_event,
            )

            if "error" in result:
                self.log(f"❌ Migration fehlgeschlagen: {result['error']}")
                GLib.idle_add(self.nvme_progress.set_text,
                              f"Fehler: {result['error'][:50]}")
                return

            s = store.stats()

            if self._nvme_stop_event.is_set():
                self.log(f"⏹ NVMe-Kompilierung pausiert: {s['total_blocks']:,} Blöcke gespeichert")
                self.log(f"   Beim nächsten Start wird automatisch fortgesetzt")
                GLib.idle_add(self.nvme_progress.set_text,
                              f"Pausiert ({s['total_blocks']:,} Blöcke)")
            else:
                self.log(f"✅ NVMe-Kompilierung abgeschlossen:")
                self.log(f"   {s['total_blocks']:,} Blöcke | {s['disk_mb']:.1f} MB")
                self.log(f"   {s['by_type'].get('articles', 0)} Artikel | "
                         f"{s['by_type'].get('facts', 0)} Fakten | "
                         f"{s['by_type'].get('events', 0)} Ereignisse")
                self.log(f"   {s['years_indexed']} Jahre indexiert | "
                         f"{result.get('errors', 0)} Fehler")
                GLib.idle_add(self.nvme_progress.set_fraction, 1.0)
                GLib.idle_add(self.nvme_progress.set_text,
                              f"Fertig: {s['total_blocks']:,} Blöcke")

                if self.chk_cleanup.get_active():
                    self.cleanup_intermediate_data(table_name)

        except Exception as e:
            self.log(f"❌ NVMe-Kompilierung Fehler: {e}")
            self.log(f"   Typ: {type(e).__name__}")
            self.log(f"   Traceback:\n{tb.format_exc()}")
            GLib.idle_add(self.nvme_progress.set_text, f"Fehler: {str(e)[:50]}")
        finally:
            if store:
                try:
                    store.close()
                except Exception as e:
                    self.log(f"⚠️ Store close Fehler: {e}")
            GLib.idle_add(self.btn_nvme_compile.set_sensitive, True)
            GLib.idle_add(self.btn_nvme_stop.set_sensitive, False)
            GLib.idle_add(self._update_nvme_status)
        return False

    def on_dump_clicked(self, widget):
        if self.running:
            self.log("⚠️ Bereits aktiv – bitte warten.")
            return
        self.running = True
        self.paused = False
        GLib.idle_add(self._set_busy_ui, True)
        self.btn_pause.set_sensitive(True)
        self.btn_resume.set_sensitive(False)
        self.thread = threading.Thread(target=self.download_dumps, daemon=True)
        self.thread.start()

    def download_dumps(self):
        """Wikipedia-Vollarchive: sequentiell herunterladen → verarbeiten → löschen."""
        self.log("📦 WIKIPEDIA DUMPS – Allgemeinwissen (sequentiell)")
        self.log("   Download → Ingest → Löschen → Nächste Datei\n")

        dump_dir = DATA_DIR / "dumps"
        dumps = list(WIKIMEDIA_DUMP_OPTIONAL) + list(WIKIMEDIA_DUMP_LEGACY_FULL)

        # Dynamic discovery of all local .xml.bz2 files in the dumps directory
        if dump_dir.exists():
            for f in dump_dir.glob("*.xml.bz2"):
                if f.is_file() and not any(d["file"] == f.name for d in dumps):
                    name_clean = f.name.replace("-articles.xml.bz2", "").replace(".xml.bz2", "").upper()
                    dumps.append({
                        "name": f"Local Dump: {name_clean}",
                        "size": f"{f.stat().st_size / (1024**3):.2f} GB",
                        "url": "",
                        "file": f.name
                    })

        processed = set(self.state.data.get("processed_dumps", []))
        venv_python = BASE_DIR / ".venv" / "bin" / "python"
        ingest_script = BASE_DIR / "scripts" / "ingest_single.py"

        pending = [d for d in dumps if d["file"] not in processed]
        if not pending:
            self.log("✅ Alle Dumps bereits verarbeitet – nichts zu tun.")
            self.log(f"   ({len(processed)} Dumps im Gedächtnis)")
            GLib.idle_add(self._finish_work)
            return

        self.log(f"📦 {len(pending)} Dumps offen, {len(processed)} bereits verarbeitet\n")

        for dump in pending:
            if not self.running:
                break
            self.check_pause()

            target = dump_dir / dump["file"]
            self.log(f"\n{'='*60}")
            self.log(f"📥 {dump['name']} ({dump['size']})")

            # 1. Download (mit Resume)
            if not dump["url"] and not target.exists():
                self.log(f"⚠️ Lokale Datei fehlt und keine Download-URL vorhanden: {dump['file']}")
                continue

            if not target.exists() or target.stat().st_size < 1_000_000:
                self.log(f"⬇️  Lade herunter: {dump['url']}")
                try:
                    proc = self._spawn(
                        ["wget", "-c", "-q", "--show-progress", "-O", str(target), dump["url"]],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    while proc.poll() is None and self.running:
                        if target.exists():
                            size_mb = target.stat().st_size / (1024**2)
                            self.log(f"   📊 {dump['name']}: {size_mb:.0f} MB")
                        time.sleep(30)
                    self._release_proc(proc)
                    if proc.returncode != 0:
                        self.log(f"⚠️ Download fehlgeschlagen: {dump['name']} (Code {proc.returncode})")
                        continue
                except FileNotFoundError:
                    self.log("❌ wget nicht installiert.")
                    break
            else:
                size_gb = target.stat().st_size / (1024**3)
                self.log(f"   📦 Bereits heruntergeladen ({size_gb:.2f} GB)")

            if not self.running:
                break

            # 2. Verarbeiten (Ingest in DB)
            if ingest_script.exists() and target.exists():
                self.log(f"⚡ Verarbeite: {dump['name']} → LanceDB")
                try:
                    proc = self._spawn(
                        [str(venv_python), str(ingest_script), str(target)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    for line in proc.stdout:
                        if not self.running:
                            proc.terminate()
                            break
                        if line.strip():
                            self.log(f"  📄 {line.strip()[:120]}")
                    proc.wait()
                    self._release_proc(proc)
                    if proc.returncode != 0:
                        self.log(f"⚠️ Ingest fehlgeschlagen: {dump['name']} (Code {proc.returncode})")
                        continue
                except Exception as e:
                    self.log(f"⚠️ Ingest-Fehler: {e}")
                    continue
            else:
                self.log(f"⚠️ ingest_single.py oder Datei fehlt")
                continue

            if not self.running:
                break

            # 3. Gedächtnis aktualisieren
            if "processed_dumps" not in self.state.data:
                self.state.data["processed_dumps"] = []
            self.state.data["processed_dumps"].append(dump["file"])
            self.state.save()

            # 4. Quelldatei löschen → Speicherplatz freigeben
            try:
                size_gb = target.stat().st_size / (1024**3)
                target.unlink()
                self.log(f"🗑️ Gelöscht: {dump['file']} ({size_gb:.2f} GB frei)")
            except Exception as e:
                self.log(f"⚠️ Löschen fehlgeschlagen: {e}")

            self.log(f"✅ {dump['name']} komplett verarbeitet")

        self.log("\n🔧 Alle Dumps verarbeitet. Starte automatische DB-Kompaktierung...")
        try:
            subprocess.run(
                [str(venv_python), str(BASE_DIR / "scripts" / "compact_db.py")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.log("✅ DB-Kompaktierung erfolgreich abgeschlossen.")
        except Exception as e:
            pass

        self.log(f"\n📦 Dump-Verarbeitung abgeschlossen.")
        total_done = len(self.state.data.get("processed_dumps", []))
        self.log(f"   {total_done}/{len(dumps)} Dumps verarbeitet")
        GLib.idle_add(self._finish_work)

    def _process_one_se_site(self, dump: dict, se_dir: Path):
        processed_se = set(self.state.data.get("processed_se", []))
        if dump["site"] in processed_se:
            self.log(f"✅ {dump['name']} bereits verarbeitet – überspringe.")
            return

        if not self._download_one_stackexchange(dump, se_dir):
            return

        if not self.running:
            return

        venv_python = BASE_DIR / ".venv" / "bin" / "python"
        ingest_script = BASE_DIR / "scripts" / "ingest_stackexchange.py"
        self.log(f"⚡ Ingestiere {dump['name']} in die Datenbank...")
        try:
            proc = self._spawn(
                [str(venv_python), str(ingest_script), dump["site"]],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                if line.strip() and self.running:
                    self.log(f"  {line.strip()[:120]}")
            proc.wait()
            self._release_proc(proc)
        except Exception as e:
            self.log(f"⚠️ Ingest-Fehler bei {dump['name']}: {e}")

        target = se_dir / dump["file"]
        site_d = se_dir / dump["site"]
        try:
            if target.exists():
                target.unlink()
            if site_d.exists():
                import shutil
                shutil.rmtree(site_d)
            self.log(f"🗑️ Temporäre StackExchange-Dateien für {dump['name']} gelöscht (Speicherplatz freigegeben)")
        except Exception as e:
            self.log(f"⚠️ Fehler beim Bereinigen von {dump['name']}: {e}")

        if "processed_se" not in self.state.data:
            self.state.data["processed_se"] = []
        if dump["site"] not in self.state.data["processed_se"]:
            self.state.data["processed_se"].append(dump["site"])
            self.state.save()
        self.update_disk_space()

    def collect_stackexchange_quality(self):
        """Nur IT-relevante SE-Sites, in sinnvoller Reihenfolge."""
        self.log("\n[STACK EXCHANGE – Qualitätsreihenfolge]")
        by_site = {d["site"]: d for d in STACKEXCHANGE_DUMPS}
        se_dir = DATA_DIR / "stackexchange"
        se_dir.mkdir(parents=True, exist_ok=True)

        for site in STACKEXCHANGE_QUALITY_ORDER:
            if not self.running:
                break
            self.check_pause()
            dump = by_site.get(site)
            if not dump:
                continue
            self._process_one_se_site(dump, se_dir)

    def collect_stackexchange_universal(self):
        """Allgemeinwissen-SE: Physik, Mathe, Bio, Chemie, Geschichte, Recht etc."""
        self.log("\n[STACK EXCHANGE – Universelles Wissen]")
        se_dir = DATA_DIR / "stackexchange"
        se_dir.mkdir(parents=True, exist_ok=True)

        for dump in STACKEXCHANGE_UNIVERSAL:
            if not self.running:
                break
            self.check_pause()
            self._process_one_se_site(dump, se_dir)

    def collect_universal_git_docs(self):
        """OpenStax-Lehrbücher und weitere universelle Git-Quellen."""
        self.log("\n[UNIVERSELLE LEHRBÜCHER – OpenStax]")
        try:
            from rag_core.bulk_download import _clone_git_source
            for src in GIT_SOURCES_UNIVERSAL:
                if not self.running:
                    break
                self.check_pause()
                target = DATA_DIR / src["subdir"]
                if target.exists() and any(target.iterdir()):
                    self.log(f"✅ {src['name']} bereits vorhanden")
                    continue
                self.log(f"⬇️  {src['name']}…")
                try:
                    _clone_git_source(src, DATA_DIR, self.log)
                except Exception as e:
                    self.log(f"⚠️ {src['name']}: {e}")
        except ImportError:
            self.log("⚠️ bulk_download Modul nicht verfügbar")

    def _download_one_stackexchange(self, dump: dict, se_dir: Path) -> bool:
        target = se_dir / dump["file"]
        site_d = se_dir / dump["site"]
        if site_d.exists() and any(site_d.rglob("Posts.xml")):
            self.log(f"✅ {dump['name']} bereits vorhanden")
            return True
        if not target.exists() or target.stat().st_size < 1_000_000:
            self.log(f"⬇️  {dump['name']} ({dump['size']}) – Starte parallelen Multi-Thread-Download…")
            try:
                import requests
                from concurrent.futures import ThreadPoolExecutor
                
                headers_resp = requests.head(dump["url"], allow_redirects=True, timeout=10)
                total_size = int(headers_resp.headers.get("content-length", 0))
                
                if total_size <= 0:
                    raise ValueError("Content-Length not reported")
                
                num_connections = 8
                chunk_size = total_size // num_connections
                ranges = []
                for i in range(num_connections):
                    start = i * chunk_size
                    end = total_size if i == num_connections - 1 else (i + 1) * chunk_size - 1
                    ranges.append((start, end))
                
                parts = [target.with_suffix(f".part{i}") for i in range(num_connections)]
                downloaded_bytes = [0] * num_connections
                last_time = time.time()
                last_bytes = 0
                
                def download_range(idx):
                    start, end = ranges[idx]
                    part_path = parts[idx]
                    req_headers = {"Range": f"bytes={start}-{end}"}
                    try:
                        with requests.get(dump["url"], headers=req_headers, stream=True, timeout=15) as r:
                            r.raise_for_status()
                            with open(part_path, "wb") as f:
                                for chunk in r.iter_content(chunk_size=1024*1024):
                                    if not self.running:
                                        return False
                                    f.write(chunk)
                                    downloaded_bytes[idx] += len(chunk)
                        return True
                    except Exception as e:
                        self.log(f"  ❌ Thread {idx} Fehler: {e}")
                        return False

                with ThreadPoolExecutor(max_workers=num_connections) as executor:
                    futures = [executor.submit(download_range, i) for i in range(num_connections)]
                    
                    while not all(f.done() for f in futures) and self.running:
                        time.sleep(2)
                        current_bytes = sum(downloaded_bytes)
                        current_time = time.time()
                        dt = current_time - last_time
                        if dt >= 1:
                            speed = (current_bytes - last_bytes) / (1024 * 1024) / dt
                            progress_pct = (current_bytes / total_size) * 100
                            self.log(
                                f"   📊 Fortschritt: {current_bytes / (1024*1024):.1f}/{total_size / (1024*1024):.1f} MB "
                                f"({progress_pct:.1f}%) | ⚡ {speed:.2f} MB/s"
                            )
                            last_bytes = current_bytes
                            last_time = current_time
                
                if not self.running:
                    for p in parts:
                        p.unlink(missing_ok=True)
                    return False
                
                if not all(f.result() for f in futures):
                    self.log(f"⚠️ Paralleler Download unvollständig: {dump['name']}")
                    for p in parts:
                        p.unlink(missing_ok=True)
                    return False
                
                self.log(f"📦 Zusammenfügen der {num_connections} Segmente…")
                with open(target, "wb") as outfile:
                    for p in parts:
                        with open(p, "rb") as infile:
                            outfile.write(infile.read())
                        p.unlink()
                self.log(f"✅ Download abgeschlossen: {dump['name']}")
                return True
                
            except Exception as e:
                self.log(f"⚠️ Paralleler Download fehlgeschlagen: {e}. Fallback auf wget…")
                try:
                    proc = self._spawn(
                        ["wget", "-c", "-q", "-O", str(target), dump["url"]],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    last_size = 0
                    while proc.poll() is None and self.running:
                        time.sleep(3)
                        if target.exists():
                            size_bytes = target.stat().st_size
                            size_mb = size_bytes / (1024 * 1024)
                            speed_mb = (size_bytes - last_size) / (1024 * 1024) / 3 if last_size > 0 else 0
                            last_size = size_bytes
                            self.log(f"   📊 Fortschritt: {size_mb:.1f} MB geladen | Geschwindigkeit: {speed_mb:.2f} MB/s")
                    self._release_proc(proc)
                    return proc.returncode == 0
                except Exception as ex:
                    self.log(f"❌ Fallback-Download fehlgeschlagen: {ex}")
                    return False
        extract_dir = se_dir / dump["site"]
        extract_dir.mkdir(parents=True, exist_ok=True)
        any_tool_found = False
        for cmd in (["7z", "x"], ["7za", "x"]):
            try:
                r = subprocess.run(
                    low_priority_cmd(cmd + [str(target), f"-o{extract_dir}", "-y"]),
                    capture_output=True,
                    text=True,
                    env=child_env(),
                )
                any_tool_found = True
                if r.returncode == 0:
                    self.log(f"✅ {dump['name']} extrahiert")
                    return True
                else:
                    self.log(f"⚠️ Entpacken fehlgeschlagen für {dump['name']} (Code {r.returncode})")
                    self.log(f"   Details: {r.stderr[:200]}")
            except FileNotFoundError:
                continue
        if any_tool_found:
            self.log(f"🗑️ Lösche beschädigtes Archiv: {target.name}")
            try:
                target.unlink()
            except Exception:
                pass
        else:
            self.log("⚠️ p7zip fehlt: sudo apt install p7zip-full")
        return False

    def _start_next_ingestion_legacy(self):
        """Legacy: XML-Dumps → it_knowledge, mit Löschung und Gedächtnis."""
        if self.ingest_running or not self.ingest_queue:
            return
        self.ingest_running = True
        target = self.ingest_queue.pop(0)
        remaining = len(self.ingest_queue)
        self.log(f"⚡ Legacy-Ingestion: {target.name} (Queue: {remaining})")

        def _run():
            ingest_script = BASE_DIR / "scripts" / "ingest_single.py"
            venv_python = BASE_DIR / ".venv" / "bin" / "python"
            success = False
            try:
                proc = self._spawn(
                    [str(venv_python), str(ingest_script), str(target)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout:
                    if line.strip():
                        self.log(f"  📄 {line.strip()[:100]}")
                proc.wait()
                self._release_proc(proc)
                success = proc.returncode == 0
                if not success:
                    self.log(f"❌ Ingestion fehlgeschlagen (Exit-Code {proc.returncode}): {target.name}")
            except Exception as e:
                self.log(f"⚠️ Ingestion: {e}")
            if success and target.exists():
                try:
                    size_gb = target.stat().st_size / (1024**3)
                    target.unlink()
                    self.log(f"🗑️ Quelldatei gelöscht: {target.name} ({size_gb:.2f} GB frei)")
                except Exception as e:
                    self.log(f"⚠️ Löschen: {e}")
                if "processed_dumps" not in self.state.data:
                    self.state.data["processed_dumps"] = []
                if target.name not in self.state.data["processed_dumps"]:
                    self.state.data["processed_dumps"].append(target.name)
                    self.state.save()
            self.ingest_running = False
            self._start_next_ingestion_legacy()

        threading.Thread(target=_run, daemon=True).start()

    def _start_next_ingestion(self):
        if self.ingest_running or not self.ingest_queue:
            return
        self.ingest_running = True
        target = self.ingest_queue.pop(0)
        remaining = len(self.ingest_queue)
        self.log(f"⚡ Ingestion gestartet: {target.name} (Queue: {remaining} wartend)")

        def _run():
            ingest_script = BASE_DIR / "scripts" / "ingest_single.py"
            venv_python = BASE_DIR / ".venv" / "bin" / "python"
            try:
                proc = self._spawn(
                    [str(venv_python), str(ingest_script), str(target)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout:
                    if line.strip():
                        self.log(f"  📄 {line.strip()}")
                proc.wait()
                self._release_proc(proc)
                success = proc.returncode == 0
                if not success:
                    self.log(f"❌ Ingestion fehlgeschlagen (Exit-Code {proc.returncode}): {target.name}")
                if success and target.exists():
                    try:
                        target.unlink()
                        self.log(f"🗑️ Quelldatei gelöscht: {target.name}")
                    except Exception as e:
                        self.log(f"⚠️ Löschen: {e}")
            except Exception as e:
                self.log(f"⚠️ Ingestion Fehler: {e}")
            finally:
                self.ingest_running = False
                self._start_next_ingestion()

        threading.Thread(target=_run, daemon=True).start()

    def download_archive_org_books(self):
        self.log("\n[ARCHIVE.ORG - IT-Fachbücher]")
        books_dir = DATA_DIR / "books"
        books_dir.mkdir(parents=True, exist_ok=True)

        collections = [
            ("folkscanomy_computer", "Computer & IT Bücher"),
            ("opensource_textbooks", "Open-Source Lehrbücher"),
        ]

        ia_bin = BASE_DIR / ".venv" / "bin" / "ia"
        if not ia_bin.exists():
            self.log("⚠️ internetarchive nicht installiert, überspringe Bücher.")
            return

        for coll_id, coll_name in collections:
            if not self.running: break
            coll_dir = books_dir / coll_id

            if coll_dir.exists() and any(coll_dir.iterdir()):
                self.log(f"✅ {coll_name} bereits vorhanden, übersprungen.")
                continue

            self.log(f"⬇️  {coll_name} (kann mehrere Stunden dauern)...")
            coll_dir.mkdir(parents=True, exist_ok=True)

            try:
                process = self._spawn(
                    [
                        str(ia_bin), "download",
                        "--search", f"collection:{coll_id}",
                        "--glob=*.pdf", "--checksum",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=str(coll_dir),
                )

                file_count = 0
                for line in process.stdout:
                    if not self.running:
                        process.terminate()
                        break
                    line = line.strip()
                    if line:
                        file_count += 1
                        if file_count % 50 == 0:
                            self.log(f"   {file_count} Dateien heruntergeladen...")

                process.wait()
                self._release_proc(process)
                self.log(f"✅ {coll_name}: {file_count} Dateien geladen.")
            except Exception as e:
                self.log(f"⚠️ Archive.org Fehler: {e}")

    def collect_all_git_docs(self):
        """Offizielle Docs, OWASP, Arch Wiki (Git shallow clone)."""
        try:
            from rag_core.bulk_download import download_all_git
            download_all_git(
                DATA_DIR,
                self.log,
                lambda: self.running,
            )
        except ImportError:
            self.log("⚠️ rag_core nicht gefunden – starte aus rag-it-knowledge/")

    def collect_stackexchange_dumps(self):
        """Stack Exchange Posts von Archive.org (7z)."""
        try:
            from rag_core.bulk_download import download_all_stackexchange
            from rag_core.bulk_sources import STACKEXCHANGE_DUMPS

            self.log("\n[STACK EXCHANGE – Archive.org]")
            se_dir = DATA_DIR / "stackexchange"
            se_dir.mkdir(parents=True, exist_ok=True)

            for dump in STACKEXCHANGE_DUMPS:
                if not self.running:
                    break
                self.check_pause()
                target = se_dir / dump["file"]
                site_d = se_dir / dump["site"]
                if site_d.exists() and any(site_d.rglob("Posts.xml")):
                    self.log(f"✅ {dump['name']} bereits extrahiert")
                    continue
                if target.exists() and target.stat().st_size > 1_000_000:
                    self.log(f"📦 Extrahiere vorhandenes Archiv: {dump['name']}…")
                else:
                    self.log(f"⬇️  {dump['name']} ({dump['size']}) …")
                    try:
                        proc = self._spawn(
                            ["wget", "-c", "-q", "-O", str(target), dump["url"]],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        last_size = 0
                        while proc.poll() is None and self.running:
                            time.sleep(3)
                            if target.exists():
                                size_bytes = target.stat().st_size
                                size_mb = size_bytes / (1024 * 1024)
                                speed_mb = (size_bytes - last_size) / (1024 * 1024) / 3 if last_size > 0 else 0
                                last_size = size_bytes
                                self.log(f"   📊 Fortschritt: {size_mb:.1f} MB geladen | Geschwindigkeit: {speed_mb:.2f} MB/s")
                        self._release_proc(proc)
                        if proc.returncode != 0:
                            self.log(f"⚠️ Download fehlgeschlagen: {dump['name']}")
                            continue
                    except FileNotFoundError:
                        self.log("❌ wget nicht installiert")
                        return

                extract_dir = se_dir / dump["site"]
                extract_dir.mkdir(parents=True, exist_ok=True)
                any_tool_found = False
                for cmd in (["7z", "x"], ["7za", "x"]):
                    try:
                        r = subprocess.run(
                            low_priority_cmd(cmd + [str(target), f"-o{extract_dir}", "-y"]),
                            capture_output=True,
                            text=True,
                            env=child_env(),
                        )
                        any_tool_found = True
                        if r.returncode == 0:
                            self.log(f"✅ {dump['name']} extrahiert")
                            break
                        else:
                            self.log(f"⚠️ Entpacken fehlgeschlagen für {dump['name']} (Code {r.returncode})")
                            self.log(f"   Details: {r.stderr[:200]}")
                    except FileNotFoundError:
                        continue
                else:
                    if any_tool_found:
                        self.log(f"🗑️ Lösche beschädigtes Archiv: {target.name}")
                        try:
                            target.unlink()
                        except Exception:
                            pass
                    else:
                        self.log("⚠️ p7zip fehlt: sudo apt install p7zip-full")

        except ImportError as e:
            self.log(f"⚠️ Stack Exchange Modul: {e}")
        except Exception as e:
            self.log(f"⚠️ Stack Exchange: {e}")
        else:
            if self.running:
                self.log("⚡ Stack Exchange → LanceDB (Hintergrund)…")
                self._run_ingest_stackexchange()

    def _run_ingest_stackexchange(self):
        venv_python = BASE_DIR / ".venv" / "bin" / "python"
        script = BASE_DIR / "scripts" / "ingest_stackexchange.py"
        if not script.exists():
            return
        try:
            self._spawn(
                [str(venv_python), str(script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def collect_mdn_docs(self):
        self.log("\n[MDN WEB DOCS - Die Bibel der Webentwicklung]")
        mdn_dir = DATA_DIR / "mdn-web-docs"
        
        if (mdn_dir / "files").exists():
            self.log("✅ MDN Docs bereits vorhanden, übersprungen.")
            return
            
        self.log("⬇️  Klone Mozilla Developer Network (14.000+ Artikel)...")
        try:
            subprocess.run(
                low_priority_cmd(
                    ["git", "clone", "--depth", "1", "https://github.com/mdn/content.git", str(mdn_dir)]
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                env=child_env(),
            )
            self.log("✅ MDN Web Docs erfolgreich geklont.")
        except Exception as e:
            self.log(f"⚠️ MDN Download Fehler: {e}")

    def collect_tldr_pages(self):
        self.log("\n[TLDR PAGES - CLI Kommando-Beispiele]")
        tldr_dir = DATA_DIR / "tldr-pages"
        
        if (tldr_dir / "pages").exists():
            self.log("✅ TLDR Pages bereits vorhanden, übersprungen.")
            return
            
        self.log("⬇️  Klone TLDR Pages (Tausende Kommandozeilen-Referenzen)...")
        try:
            subprocess.run(
                low_priority_cmd(
                    ["git", "clone", "--depth", "1", "https://github.com/tldr-pages/tldr.git", str(tldr_dir)]
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                env=child_env(),
            )
            self.log("✅ TLDR Pages erfolgreich geklont.")
        except Exception as e:
            self.log(f"⚠️ TLDR Download Fehler: {e}")

    def download_gutenberg(self):
        self.log("\n[PROJECT GUTENBERG - 70.000+ Bücher]")
        gut_dir = DATA_DIR / "gutenberg"
        gut_dir.mkdir(parents=True, exist_ok=True)

        self.log("⬇️  Starte/Fortsetze rsync von Project Gutenberg (nur .txt Dateien)...")
        self.log("   Dies kann je nach Verbindung mehrere Stunden dauern.")
        try:
            process = self._spawn(
                [
                    "rsync", "-av", "--timeout=120",
                    "--include=*/", "--include=*.txt", "--exclude=*",
                    "aleph.gutenberg.org::gutenberg", str(gut_dir),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            count = 0
            for line in process.stdout:
                if not self.running:
                    process.terminate()
                    break
                if line.strip().endswith(".txt"):
                    count += 1
                    if count % 500 == 0:
                        self.log(f"   {count} Bücher synchronisiert...")

            process.wait()
            self._release_proc(process)
            if process.returncode == 0:
                self.log(f"✅ Project Gutenberg: {count} Bücher geladen.")
            else:
                self.log(f"⚠️ Gutenberg rsync beendet mit Code {process.returncode}")
        except FileNotFoundError:
            self.log("⚠️ rsync nicht installiert, Gutenberg übersprungen.")
        except Exception as e:
            self.log(f"⚠️ Gutenberg Fehler: {e}")

    def download_linux_docs(self):
        self.log("\n[LINUX KERNEL DOKUMENTATION]")
        docs_dir = DATA_DIR / "linux-docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # Prüfe ob Documentation-Ordner bereits extrahiert wurde
        if (docs_dir / "Documentation").exists():
            self.log("✅ Linux-Docs bereits vorhanden, übersprungen.")
            return

        # Versuche linux-doc Paket zu nutzen
        src = Path("/usr/share/doc/linux-doc")
        if src.exists():
            self.log("📋 Kopiere vorinstallierte Linux Kernel Docs...")
            try:
                subprocess.run(["cp", "-r", str(src), str(docs_dir / "Documentation")], check=True)
                self.log("✅ Linux Kernel Docs kopiert.")
                return
            except:
                pass

        # Alternativ: Kernel Source Docs als Tarball
        tarball = docs_dir / "linux-kernel.tar.xz"
        self.log("⬇️  Lade Linux Kernel Source (Documentation-Ordner)...")
        try:
            result = subprocess.run(
                ["wget", "-c", "-q",
                 "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.tar.xz",
                 "-O", str(tarball)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

            if result.returncode == 0 and tarball.exists():
                size_mb = tarball.stat().st_size / (1024**2)
                self.log(f"📦 Download fertig ({size_mb:.0f} MB). Extrahiere Documentation-Ordner...")
                subprocess.run([
                    "tar", "xf", str(tarball),
                    "--wildcards", "*/Documentation/*",
                    "-C", str(docs_dir), "--strip-components=1"
                ], stderr=subprocess.DEVNULL)
                tarball.unlink(missing_ok=True)
                self.log("✅ Linux Kernel Docs extrahiert.")
            else:
                self.log(f"⚠️ Kernel-Download fehlgeschlagen (Code {result.returncode})")
        except Exception as e:
            self.log(f"⚠️ Linux-Docs Fehler: {e}")

    def on_key_changed(self, widget):
        self.state.data["so_key"] = widget.get_text()
        self.state.save()

    def check_pause(self):
        while self.paused and self.running:
            time.sleep(1)

    def collect_loop(self):
        self.log("API-Sammlung (ergänzend) – für volle DB: «🎯 Optimale DB aufbauen»")
        
        while self.running:
            try:
                self.collect_wikipedia()
                if not self.running:
                    break
                if self.paused:
                    self.check_pause()

                self.collect_stackoverflow()
                if not self.running:
                    break
                if self.paused:
                    self.check_pause()

                self.log("♻️ API-Zyklus fertig. Pause 10 Min…")
                for _ in range(600):
                    if not self.running or self.paused: break
                    time.sleep(1)
                self.check_pause()
                    
            except Exception as e:
                self.log(f"❌ Fehler im Hauptloop: {str(e)}")
                time.sleep(30)
                
        self.log("🛑 Sammel-Prozess beendet.")
        GLib.idle_add(self._finish_work)

    def collect_wikipedia(self):
        self.log("\n[WIKIPEDIA API – domänengefiltertes Sammeln]")
        if len(self.state.data.get("wiki_done", [])) > WIKI_MAX_DONE:
            self.log(f"✅ Bereits {WIKI_MAX_DONE}+ Artikel – übersprungen.")
            return

        wiki_keywords = get_wiki_link_keywords()
        wiki_blocked = get_wiki_blocked_patterns()
        self.log(f"  📋 {len(wiki_keywords)} Link-Keywords, {len(wiki_blocked)} Blockmuster aktiv")

        wiki = wikipediaapi.Wikipedia(user_agent='CustomRAG-Collector/3.0', language='de')
        wiki_dir = DATA_DIR / "wikipedia"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        fetched = 0
        skipped_noise = 0

        while self.state.data["wiki_queue"] and self.running:
            if len(self.state.data["wiki_queue"]) > WIKI_MAX_QUEUE:
                self.state.data["wiki_queue"] = self.state.data["wiki_queue"][:WIKI_MAX_QUEUE]
            if len(self.state.data.get("wiki_done", [])) >= WIKI_MAX_DONE:
                self.log(f"✅ Limit {WIKI_MAX_DONE} Artikel erreicht.")
                break

            self.check_pause()
            topic = self.state.data["wiki_queue"].pop(0)
            if topic in self.state.data["wiki_done"]:
                continue

            topic_lower = topic.lower()
            if any(bp in topic_lower for bp in wiki_blocked):
                skipped_noise += 1
                self.state.data["wiki_done"].append(topic)
                continue

            try:
                page = wiki.page(topic)
                if page.exists() and len(page.text) > 200:
                    file_path = wiki_dir / f"{topic.replace('/', '_')}.txt"
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(f"# {page.title}\n\n{page.text}\n")
                    fetched += 1
                    if fetched % 25 == 0:
                        self.log(f"  … {fetched} Artikel (Queue: {len(self.state.data['wiki_queue'])}, Rauschen gefiltert: {skipped_noise})")

                    for link_title in page.links.keys():
                        tl = link_title.lower()
                        if any(bp in tl for bp in wiki_blocked):
                            continue
                        if any(kw.lower() in tl for kw in wiki_keywords):
                            if (
                                link_title not in self.state.data["wiki_done"]
                                and link_title not in self.state.data["wiki_queue"]
                                and len(self.state.data["wiki_queue"]) < WIKI_MAX_QUEUE
                            ):
                                self.state.data["wiki_queue"].append(link_title)
            except Exception as e:
                self.log(f"Wiki: {topic[:40]} – {e}")

            self.state.data["wiki_done"].append(topic)
            self.state.save()
            time.sleep(2.5)

        if skipped_noise:
            self.log(f"  🧹 {skipped_noise} Rausch-Artikel übersprungen")
        if not self.state.data["wiki_queue"] and self.running:
            self.state.data["wiki_queue"] = self.state.wiki_topics.copy()
            self.state.save()

    def collect_stackoverflow(self):
        self.log(f"\n[STACKOVERFLOW API – max. {SO_MAX_PAGES_PER_TAG} Seiten/Tag]")
        so_dir = DATA_DIR / "stackoverflow"
        so_dir.mkdir(parents=True, exist_ok=True)

        for tag in self.state.so_tags:
            if not self.running:
                return
            page_num = self.state.data["so_progress"].get(tag, 1)
            if page_num > SO_MAX_PAGES_PER_TAG:
                continue

            while page_num <= SO_MAX_PAGES_PER_TAG:
                self.check_pause()
                if not self.running:
                    return

                self.log(f"SO: '{tag}' (Seite {page_num}/{SO_MAX_PAGES_PER_TAG})…")
                url = f"https://api.stackexchange.com/2.3/questions?page={page_num}&pagesize=100&order=desc&sort=votes&tagged={tag}&site=stackoverflow&filter=withbody"
                
                so_key = self.state.data.get("so_key", "")
                if so_key:
                    url += f"&key={so_key}"
                
                try:
                    resp = requests.get(url, timeout=30)
                    data = resp.json()
                    
                    if "backoff" in data:
                        wait = data["backoff"]
                        self.log(f"⏳ API-Backoff: {wait}s warten...")
                        time.sleep(wait)
                    
                    if "error_id" in data:
                        error_msg = data.get('error_message', 'Unbekannter Fehler')
                        if data.get('error_id') == 403:
                            self.log(f"⚠️ SO Limit erreicht bei '{tag}' Seite {page_num}: {error_msg}")
                            if not so_key:
                                self.log("→ Trage einen API-Key ein, um das Limit zu erhöhen.")
                            return
                        else:
                            self.log(f"SO API Fehler {data.get('error_id')}: {error_msg}")
                        break
                        
                    if "items" in data and len(data["items"]) > 0:
                        file_path = so_dir / f"top_{tag}_page{page_num}.txt"
                        with open(file_path, "w", encoding="utf-8") as f:
                            for item in data["items"]:
                                f.write(f"Frage: {item.get('title', '')}\n")
                                f.write(f"Tags: {', '.join(item.get('tags', []))}\n")
                                f.write(f"Inhalt:\n{item.get('body', '')}\n\n{'='*80}\n\n")
                    
                    if not data.get("has_more", False):
                        self.log(f"✅ Tag '{tag}' komplett abgeschlossen.")
                        break
                        
                except requests.exceptions.RequestException as e:
                    self.log(f"SO Netzwerk-Fehler: {e}")
                    time.sleep(10)
                    continue
                except Exception as e:
                    self.log(f"SO Fehler: {e}")
                    break
                
                page_num += 1
                self.state.data["so_progress"][tag] = page_num
                self.state.save()
                time.sleep(2) 

    def collect_rfcs(self):
        self.check_pause()
        if not self.running: return
        
        self.log("\n[RFCs - KOMPLETT]")
        self.log("Synchronisiere RFC Archiv via rsync...")
        rfc_dir = DATA_DIR / "rfcs"
        try:
            result = subprocess.run(
                low_priority_cmd([
                    "rsync", "-avz", "--timeout=60",
                    "rsync.rfc-editor.org::rfcs-text-only", str(rfc_dir),
                ]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                env=child_env(),
            )
            if result.returncode == 0:
                self.log("✅ RFC-Sync abgeschlossen.")
            else:
                self.log(f"⚠️ RFC-Sync beendet mit Code {result.returncode}: {result.stderr[:200]}")
        except FileNotFoundError:
            self.log("❌ rsync nicht installiert. Installiere mit: sudo apt install rsync")
        except Exception as e:
            self.log(f"❌ RFC Sync Fehler: {e}")

    def collect_manpages(self):
        self.log("\n[MANPAGES]")
        man_dir = DATA_DIR / "manpages"
        man_dir.mkdir(parents=True, exist_ok=True)
        
        env = {**os.environ, "TERM": "dumb", "COLUMNS": "120", "MAN_KEEP_FORMATTING": "1"}
        
        for section in self.state.man_sections:
            self.check_pause()
            if not self.running: break
            
            if section in self.state.data.get("man_done", []): continue
            
            self.log(f"Exportiere Manpage Section {section}...")
            try:
                res = subprocess.run(["man", "-k", "."], capture_output=True, text=True, env=env)
                pages = [line.split("(")[0].strip() for line in res.stdout.splitlines() if f"({section})" in line]
                
                count = 0
                for page in pages:
                    self.check_pause()
                    if not self.running: break
                    
                    safe_name = page.replace("/", "_")
                    file_path = man_dir / f"{safe_name}.{section}.txt"
                    if not file_path.exists():
                        try:
                            with open(file_path, "w") as f:
                                subprocess.run(["man", f"{section}", page], stdout=f, stderr=subprocess.DEVNULL, env=env, timeout=10)
                            count += 1
                        except subprocess.TimeoutExpired:
                            pass
                
                self.log(f"  → Section {section}: {count} neue Manpages exportiert.")
                if "man_done" not in self.state.data:
                    self.state.data["man_done"] = []
                self.state.data["man_done"].append(section)
                self.state.save()
            except Exception as e:
                self.log(f"Manpage Fehler Section {section}: {e}")

if __name__ == "__main__":
    if not _acquire_pid_lock():
        print("\u274c Collector l\u00e4uft bereits (PID-Lock aktiv). Schlie\u00dfe das andere Fenster zuerst.")
        d = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Collector l\u00e4uft bereits!",
        )
        d.format_secondary_text("Schlie\u00dfe das andere Collector-Fenster zuerst.")
        d.run()
        d.destroy()
        sys.exit(1)
    atexit.register(_release_pid_lock)
    win = RAGCollectorGUI()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    _release_pid_lock()
