import atexit
import os
import signal
import sys
from pathlib import Path
import shutil
import subprocess
import threading
import time

try:
    import trafilatura
except ImportError:
    trafilatura = None

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

# Speicherpfad laden
try:
    import json
    _state_file = BASE_DIR / "collector_state.json"
    if _state_file.exists():
        with open(_state_file, "r", encoding="utf-8") as _f:
            _saved = json.load(_f)
            if isinstance(_saved, dict):
                _saved_dir = _saved.get("data_dir")
                if _saved_dir:
                    os.environ["RAG_DATA_DIR"] = _saved_dir
except Exception:
    pass

DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", str(BASE_DIR / "data"))) / "custom_docs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE = BASE_DIR / ".custom_collector.pid"


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

class CustomCollectorGUI(Gtk.Window):
    def __init__(self):
        super().__init__(title="Custom RAG Training")
        self.set_default_size(720, 520)
        self.set_border_width(15)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.running = False
        self._child_procs = []
        
        self.connect("delete-event", self.on_window_delete)
        
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(vbox)
        
        header = Gtk.Label()
        header.set_markup("<big><b>🧠 Custom KI-Wissen</b></big>\n<small>Lade eigene PDFs/Textdokumente hoch oder füge einen Link ein, um ihn direkt zu trainieren.</small>")
        vbox.pack_start(header, False, False, 5)
        
        # --- LINK INPUT ---
        hbox_link = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_link = Gtk.Entry()
        self.entry_link.set_placeholder_text("https://example.com/article...")
        hbox_link.pack_start(self.entry_link, True, True, 0)
        
        self.btn_download = Gtk.Button(label="📥 Link abrufen")
        self.btn_download.connect("clicked", self.on_download_clicked)
        hbox_link.pack_start(self.btn_download, False, False, 0)
        vbox.pack_start(hbox_link, False, False, 5)
        
        # --- UPLOAD ---
        hbox_upload = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_upload = Gtk.Button(label="📄 Dokument hochladen (PDF, TXT, MD)")
        self.btn_upload.connect("clicked", self.on_upload_clicked)
        hbox_upload.pack_start(self.btn_upload, True, True, 0)

        self.btn_upload_folder = Gtk.Button(label="📁 Ordner hochladen")
        self.btn_upload_folder.connect("clicked", self.on_upload_folder_clicked)
        hbox_upload.pack_start(self.btn_upload_folder, True, True, 0)
        vbox.pack_start(hbox_upload, False, False, 0)
        
        # --- EXPORT ---
        self.btn_export = Gtk.Button(label="💾 DB speichern (Name vergeben) & Neue anlegen")
        self.btn_export.connect("clicked", self.on_export_clicked)
        vbox.pack_start(self.btn_export, False, False, 5)
        
        # --- CHAT LAUNCHER ---
        self.btn_chat = Gtk.Button(label="💬 Mit der KI chatten (Wissens-Chat öffnen)")
        self.btn_chat.get_style_context().add_class("suggested-action")
        self.btn_chat.connect("clicked", self.on_chat_clicked)
        vbox.pack_start(self.btn_chat, False, False, 15)
        
        # --- ZIEL DATENBANK AUSWÄHLEN ---
        hbox_target = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox_target.pack_start(Gtk.Label(label="Ziel-Datenbank:"), False, False, 0)
        
        self.db_combo = Gtk.ComboBoxText()
        db_dir = BASE_DIR / "lancedb_index"
        has_dbs = False
        if db_dir.exists():
            for d in db_dir.glob("*.lance"):
                if d.is_dir():
                    self.db_combo.append(d.stem, d.stem)
                    has_dbs = True
        if not has_dbs:
            self.db_combo.append("it_prime", "it_prime")
        self.db_combo.set_active(0)
        hbox_target.pack_start(self.db_combo, True, True, 0)
        vbox.pack_start(hbox_target, False, False, 5)
        
        # --- TRAIN ---
        hbox_train = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        
        self.btn_train = Gtk.Button(label="⚡ Neu trainieren (Überschreiben)")
        self.btn_train.get_style_context().add_class("destructive-action")
        self.btn_train.connect("clicked", self.on_train_clicked, True)
        hbox_train.pack_start(self.btn_train, True, True, 0)

        self.btn_extend = Gtk.Button(label="➕ Datenbank erweitern")
        self.btn_extend.get_style_context().add_class("suggested-action")
        self.btn_extend.connect("clicked", self.on_train_clicked, False)
        hbox_train.pack_start(self.btn_extend, True, True, 0)
        
        vbox.pack_start(hbox_train, False, False, 10)
        
        # --- AUTO NVME OPTIONS ---
        self.chk_auto_nvme = Gtk.CheckButton(label="Nach dem Training automatisch in das schnelle NVMe-Format kompilieren")
        self.chk_auto_nvme.set_active(True)
        self.chk_auto_nvme.set_tooltip_text("Kompiliert das neu gelernte Wissen direkt ins extrem schnelle NVMe-Format für die CPU-KI.")
        vbox.pack_start(self.chk_auto_nvme, False, False, 4)

        self.chk_cleanup = Gtk.CheckButton(label="Nach erfolgreicher NVMe-Kompilierung temporäre Trainingsdaten & LanceDB löschen (saubere Festplatte)")
        self.chk_cleanup.set_active(True)
        self.chk_cleanup.set_tooltip_text("Löscht hochgeladene Textdateien in data/custom_docs/ und die temporäre it_prime Tabelle, da alles sicher im NVMe-Store liegt.")
        vbox.pack_start(self.chk_cleanup, False, False, 4)

        self.nvme_status = Gtk.Label()
        self.nvme_status.set_halign(Gtk.Align.START)
        self._update_nvme_status()
        vbox.pack_start(self.nvme_status, False, False, 2)

        self.nvme_progress = Gtk.ProgressBar()
        self.nvme_progress.set_show_text(True)
        self.nvme_progress.set_text("Bereit")
        vbox.pack_start(self.nvme_progress, False, False, 6)
        
        # --- LOGS ---
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        # Styling wird nun über CSS (rag_core/gui_theme) geregelt
        scrolled.add(self.textview)
        vbox.pack_start(scrolled, True, True, 0)
        
        self.log("✅ Eigenes KI-Training bereit.\nFüge einen Web-Link ein oder lade Dokumente hoch.")
        if trafilatura is None:
            self.log("⚠️ Warnung: 'trafilatura' fehlt für Website-Downloads (wird via pip install nachgeladen).")
        
    def log(self, msg):
        GLib.idle_add(self._log_gui, str(msg))
        
    def _log_gui(self, msg):
        buf = self.textview.get_buffer()
        end = buf.get_end_iter()
        buf.insert(end, msg + "\n")
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.textview.scroll_to_mark(mark, 0.05, True, 0.0, 1.0)
        return False
        
    def on_window_delete(self, widget, event):
        self.log("🛡️ Sauberer Shutdown...")
        self.running = False
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
                
        _release_pid_lock()
        Gtk.main_quit()
        return False
        
    def set_busy(self, busy):
        self.running = busy
        self.btn_download.set_sensitive(not busy)
        self.btn_upload.set_sensitive(not busy)
        self.btn_upload_folder.set_sensitive(not busy)
        self.btn_train.set_sensitive(not busy)
        self.btn_extend.set_sensitive(not busy)
        self.chk_auto_nvme.set_sensitive(not busy)
        self.chk_cleanup.set_sensitive(not busy)
        
    def on_upload_clicked(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Wähle ein Dokument", parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            dest = DATA_DIR / Path(filename).name
            try:
                shutil.copy2(filename, dest)
                self.log(f"📄 Datei gespeichert: {dest.name}")
            except Exception as e:
                self.log(f"⚠️ Fehler beim Kopieren: {e}")
        dialog.destroy()
        
    def on_upload_folder_clicked(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Wähle einen Ordner mit Dokumenten", parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            folder = dialog.get_filename()
            threading.Thread(target=self._upload_folder_worker, args=(folder,), daemon=True).start()
        dialog.destroy()

    def _upload_folder_worker(self, folder_path):
        self.log(f"📁 Durchsuche Ordner: {folder_path} ...")
        copied = 0
        skipped = 0
        extensions = {
            ".pdf", ".txt", ".md", ".rst", ".json", ".js", ".jsx", ".ts", ".tsx",
            ".py", ".html", ".css", ".csv", ".xml", ".java", ".cpp", ".c", ".h",
            ".rs", ".go", ".yaml", ".yml", ".sh", ".bash"
        }
        try:
            for root_dir, _, files in os.walk(folder_path):
                if any(skip in Path(root_dir).parts for skip in {".git", "node_modules", "__pycache__", ".venv"}):
                    continue
                for file in files:
                    ext = Path(file).suffix.lower()
                    if ext in extensions:
                        src_file = Path(root_dir) / file
                        rel_path = Path(src_file).relative_to(folder_path)
                        flat_name = "_".join(rel_path.parts)
                        dest = DATA_DIR / flat_name
                        try:
                            shutil.copy2(src_file, dest)
                            copied += 1
                        except Exception as e:
                            self.log(f"⚠️ Fehler beim Kopieren von {file}: {e}")
                            skipped += 1
                    else:
                        skipped += 1
            self.log(f"✅ Ordner importiert: {copied} Dokument(e) hinzugefügt (übersprungen/andere: {skipped}).")
        except Exception as e:
            self.log(f"❌ Fehler beim Importieren des Ordners: {e}")
        
    def on_download_clicked(self, widget):
        if trafilatura is None:
            self.log("❌ Fehler: Das Modul 'trafilatura' für Web-Downloads fehlt im Environment.")
            return

        url = self.entry_link.get_text().strip()
        if not url.startswith("http"):
            self.log("⚠️ Bitte eine gültige URL (http://...) eingeben.")
            return
        
        self.set_busy(True)
        threading.Thread(target=self._download_worker, args=(url,), daemon=True).start()
        
    def _download_worker(self, url):
        self.log(f"📥 Lade Inhalte von: {url}")
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                self.log("❌ Konnte Seite nicht abrufen (ggf. Blockiert oder Offline).")
            else:
                text = trafilatura.extract(downloaded, include_links=True)
                if text:
                    safe_name = "".join(c if c.isalnum() else "_" for c in url)[-40:] + ".txt"
                    dest = DATA_DIR / safe_name
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(f"# Quelle: {url}\n\n{text}\n")
                    self.log(f"✅ Text extrahiert und gespeichert ({len(text)} Zeichen).")
                    GLib.idle_add(self.entry_link.set_text, "")
                else:
                    self.log("⚠️ Es konnte kein sinnvoller Text auf der Seite gefunden werden.")
        except Exception as e:
            self.log(f"⚠️ Netzwerk-/Abruffehler: {e}")
            
        GLib.idle_add(self.set_busy, False)
        
    def on_train_clicked(self, widget, fresh):
        self.set_busy(True)
        db_name = self.db_combo.get_active_id() or "it_prime"
        threading.Thread(target=self._train_worker, args=(fresh, db_name), daemon=True).start()
        
    def _train_worker(self, fresh, db_name):
        modus = "Neuaufbau" if fresh else "Erweitern"
        self.log(f"\n⚡ Starte KI-Training für '{db_name}' (Modus: {modus}) ...")
        venv_python = BASE_DIR / ".venv" / "bin" / "python"
        script = BASE_DIR / "scripts" / "build_prime_index.py"
        
        env = os.environ.copy()
        env["RAG_TABLE"] = db_name
        
        cmd = [str(venv_python), str(script)]
        if fresh:
            cmd.append("--fresh")
            
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env=env, cwd=str(BASE_DIR)
            )
            self._child_procs.append(proc)
            for line in proc.stdout:
                if not self.running:
                    proc.terminate()
                    break
                if line.strip():
                    self.log(line.strip())
            proc.wait()
            if proc.returncode == 0:
                self.log("✅ Training erfolgreich abgeschlossen! Die KI kennt nun deine Daten.")
                if self.chk_auto_nvme.get_active():
                    self.log("\n⚡ Starte Phase 2: Automatische NVMe-Kompilierung...")
                    self._nvme_compile_sync(db_name)
            else:
                self.log(f"⚠️ Training beendet mit Fehlern (Code {proc.returncode}).")
        except Exception as e:
            self.log(f"❌ Ausführungsfehler: {e}")
            
        GLib.idle_add(self.set_busy, False)

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
                    f"<b>Aktueller NVMe-Store:</b> {size_mb:.1f} MB | "
                    f"<b>{s['total_blocks']:,}</b> Blöcke "
                    f"({s['by_type'].get('articles', 0)} Artikel, "
                    f"{s['by_type'].get('facts', 0)} Fakten) | "
                    f"{s['years_indexed']} Jahre"
                )
            else:
                markup = "<i>NVMe-Store: noch nicht erstellt – wird beim ersten Training generiert</i>"
            GLib.idle_add(self.nvme_status.set_markup, markup)
        except Exception as e:
            GLib.idle_add(self.nvme_status.set_markup, f"<i>NVMe-Status: {e}</i>")
        return True

    def _nvme_compile_sync(self, table_name: str):
        import traceback as tb
        try:
            from rag_core.nvme_blocks import NVMeBlockStore
            from rag_core.knowledge_compiler import migrate_lancedb_to_nvme
        except ImportError as e:
            self.log(f"❌ Import-Fehler bei NVMe-Modulen: {e}")
            self.log(f"   Stelle sicher, dass nvme_blocks.py und knowledge_compiler.py existieren")
            self.log(f"   Traceback:\n{tb.format_exc()}")
            return

        store = None
        try:
            from rag_core.config import NVME_KNOWLEDGE_PATH
            store_path = NVME_KNOWLEDGE_PATH
            store = NVMeBlockStore(store_path)
            self.log(f"📂 NVMe-Store geöffnet: {store.index.total_blocks:,} bestehende Blöcke.")
        except Exception as e:
            self.log(f"❌ Store-Fehler: {e}")
            self.log(f"   Traceback:\n{tb.format_exc()}")
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

            GLib.idle_add(self.nvme_progress.set_fraction, 0.0)
            GLib.idle_add(self.nvme_progress.set_text, "Kompiliere...")

            result = migrate_lancedb_to_nvme(
                store,
                table_name=table_name,
                batch_size=1000000,
                log_fn=log_fn,
            )

            if "error" in result:
                self.log(f"❌ NVMe-Kompilierung fehlgeschlagen: {result['error']}")
                GLib.idle_add(self.nvme_progress.set_text, "Fehler")
            else:
                s = store.stats()
                self.log(f"✅ NVMe-Format erfolgreich kompiliert!")
                self.log(f"   Gesamt: {s['total_blocks']:,} Blöcke | {s['disk_mb']:.1f} MB")
                GLib.idle_add(self.nvme_progress.set_fraction, 1.0)
                GLib.idle_add(self.nvme_progress.set_text, "Kompiliert")

                if self.chk_cleanup.get_active():
                    self.cleanup_custom_intermediate_data(table_name)
        except Exception as e:
            self.log(f"❌ Fehler bei NVMe-Kompilierung: {e}\n{tb.format_exc()}")
            GLib.idle_add(self.nvme_progress.set_text, "Fehler")
        finally:
            if store:
                try:
                    store.close()
                except Exception:
                    pass
            self._update_nvme_status()

    def cleanup_custom_intermediate_data(self, table_name: str):
        self.log("\n🧹 Starte automatische Speicherbereinigung...")
        import shutil
        
        # 1. Hochgeladene Custom-Dokumente löschen
        cleaned_files = 0
        if DATA_DIR.exists():
            try:
                for f in DATA_DIR.iterdir():
                    if f.is_file():
                        f.unlink()
                        cleaned_files += 1
            except Exception as e:
                self.log(f"⚠️ Fehler beim Bereinigen von custom_docs: {e}")
                
        # 2. Temporären LanceDB-Index löschen
        db_dir = BASE_DIR / "lancedb_index" / f"{table_name}.lance"
        if db_dir.exists():
            try:
                shutil.rmtree(db_dir)
                self.log(f"🗑️ Temporärer LanceDB-Index gelöscht: {table_name}.lance")
            except Exception as e:
                self.log(f"⚠️ Fehler beim Löschen des LanceDB-Index: {e}")
                
        self.log(f"✅ Bereinigung abgeschlossen. {cleaned_files} Dokumente aus dem Puffer entfernt.")
        GLib.idle_add(self._refresh_db_combo)

    def on_export_clicked(self, widget):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Wissensdatenbank extrahieren & speichern"
        )
        dialog.format_secondary_text("Bitte gib einen Namen für die trainierte Datenbank ein (z. B. 'Projekt_Alpha').\nDies ermöglicht es, mehrere Modelle in der Chat-GUI auszuwählen.")
        
        entry = Gtk.Entry()
        entry.set_placeholder_text("Name ohne Leerzeichen...")
        dialog.get_message_area().pack_start(entry, True, True, 5)
        dialog.show_all()
        
        response = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()
        
        if response == Gtk.ResponseType.OK and name:
            safe_name = "".join(c if c.isalnum() else "_" for c in name)
            self._save_db(safe_name)

    def _save_db(self, name):
        import shutil
        src = BASE_DIR / "lancedb_index" / "it_prime.lance"
        dst = BASE_DIR / "lancedb_index" / f"{name}.lance"
        
        if not src.exists():
            self.log("⚠️ Keine frisch trainierte Datenbank (it_prime) gefunden.")
            return
            
        try:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            
            # Reset
            for f in DATA_DIR.iterdir():
                if f.is_file():
                    f.unlink()
                    
            shutil.rmtree(src)
            self.log(f"✅ Datenbank als '{name}' gespeichert!\nDie hochgeladenen Dokumente wurden entfernt.")
            
            # Update Dropdown
            GLib.idle_add(self._refresh_db_combo)
        except Exception as e:
            self.log(f"❌ Fehler beim Speichern: {e}")

    def _refresh_db_combo(self):
        self.db_combo.remove_all()
        db_dir = BASE_DIR / "lancedb_index"
        has_dbs = False
        if db_dir.exists():
            for d in db_dir.glob("*.lance"):
                if d.is_dir():
                    self.db_combo.append(d.stem, d.stem)
                    has_dbs = True
        if not has_dbs:
            self.db_combo.append("it_prime", "it_prime")
        self.db_combo.set_active(0)
        return False

    def on_chat_clicked(self, widget):
        venv_python = BASE_DIR / ".venv" / "bin" / "python"
        chat_script = BASE_DIR / "gui_chat.py"
        try:
            proc = subprocess.Popen([str(venv_python), str(chat_script)], cwd=str(BASE_DIR))
            self._child_procs.append(proc)
            self.log("🚀 Chat-Interface gestartet!")
        except Exception as e:
            self.log(f"❌ Fehler beim Starten der Chat-GUI: {e}")

if __name__ == "__main__":
    if not _acquire_pid_lock():
        print("❌ Custom RAG Training läuft bereits. Schließe das andere Fenster zuerst.")
        d = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Custom RAG Training läuft bereits!",
        )
        d.format_secondary_text("Schließe das andere Custom RAG Fenster zuerst.")
        d.run()
        d.destroy()
        sys.exit(1)
        
    atexit.register(_release_pid_lock)
    from rag_core.gui_theme import apply_theme
    apply_theme()
    win = CustomCollectorGUI()
    win.show_all()
    Gtk.main()
    _release_pid_lock()
