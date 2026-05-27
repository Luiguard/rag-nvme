#!/usr/bin/env python3
"""Hauptmenü – alles per Mausklick, kein Terminal nötig."""
from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

_BASE = Path(__file__).resolve().parent
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


from rag_core.gui_resources import child_env, resource_summary

_env = child_env()
for _k, _v in _env.items():
    if _k.startswith(("OMP_", "MKL_", "NUMEXPR_", "OPENBLAS_", "VECLIB_", "TOKENIZERS")):
        os.environ[_k] = _v

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk, Pango

from rag_core.knowledge_stats import format_stats_markup, gather_stats
from rag_core.system_check import run_system_check

VENV_PYTHON = _BASE / ".venv" / "bin" / "python"


class ProgressDialog(Gtk.Dialog):
    def __init__(self, parent, title: str):
        super().__init__(title=title, transient_for=parent, modal=True)
        self.set_default_size(520, 360)
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(280)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD)
        scroll.add(self.log_view)
        box.pack_start(scroll, True, True, 0)
        self._buf = self.log_view.get_buffer()
        self.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        self.show_all()

    def log(self, line: str):
        end = self._buf.get_end_iter()
        self._buf.insert(end, line + "\n")


class RAGMainWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Lokale IT-KI")
        self.set_default_size(520, 480)
        self.set_border_width(16)
        self._child_windows: list[Gtk.Window] = []
        self._data_bytes_hint = 0

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(vbox)

        title = Gtk.Label()
        title.set_markup(
            "<big><b>🧠 Lokale IT-KI</b></big>\n"
            "<small>Wissen · sicheres Coding · Review – komplett ohne Terminal</small>\n"
            f"<small>{GLib.markup_escape_text(resource_summary())}</small>"
        )
        vbox.pack_start(title, False, False, 0)

        self.stats_label = Gtk.Label(label="Wissensbasis wird geladen…")
        self.stats_label.set_line_wrap(True)
        self.stats_label.set_halign(Gtk.Align.START)
        stats_frame = Gtk.Frame(label=" Wissensbasis ")
        stats_frame.add(self.stats_label)
        vbox.pack_start(stats_frame, False, False, 0)

        # Wissens-Bereiche (Domänen)
        domain_frame = Gtk.Frame(label=" Wissens-Bereiche (Domänen) ")
        vbox.pack_start(domain_frame, False, False, 0)
        
        domain_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        domain_box.set_margin_start(10)
        domain_box.set_margin_end(10)
        domain_box.set_margin_top(8)
        domain_box.set_margin_bottom(8)
        domain_frame.add(domain_box)
        
        from rag_core.domains import ALL_DOMAINS
        from rag_core.quality import get_current_domains
        
        active_ids = {d.id for d in get_current_domains()}
        self._domain_checks = {}
        
        domain_grid = Gtk.Grid()
        domain_grid.set_column_homogeneous(True)
        domain_grid.set_row_spacing(6)
        domain_grid.set_column_spacing(10)
        domain_box.pack_start(domain_grid, True, True, 0)
        
        col, row = 0, 0
        for dom_id, dom in sorted(ALL_DOMAINS.items(), key=lambda x: x[0]):
            chk = Gtk.CheckButton(label=dom.name)
            chk.set_tooltip_text(dom.description)
            chk.set_active(dom_id in active_ids)
            chk.connect("toggled", self._on_domain_toggled, dom_id)
            domain_grid.attach(chk, col, row, 1, 1)
            self._domain_checks[dom_id] = chk
            col += 1
            if col > 1:
                col = 0
                row += 1

        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)
        vbox.pack_start(grid, True, True, 0)

        buttons = [
            ("💬 Assistent", "Chat mit RAG + Ollama", self._open_chat, 0, 0),
            ("📚 Kollektor", "Wissen sammeln & indexieren", self._open_collector, 0, 1),
            ("📁 Meine Projekte", "~/projects in Wissensbasis", self._index_projects, 1, 0),
            ("🔍 System prüfen", "Index, Ollama, Retrieval-Test", self._run_check, 1, 1),
        ]
        for label, tip, handler, row, col in buttons:
            btn = Gtk.Button(label=label)
            btn.set_tooltip_text(tip)
            btn.connect("clicked", handler)
            btn.set_size_request(-1, 56)
            grid.attach(btn, col, row, 1, 1)

        hint = Gtk.Label()
        hint.set_markup(
            "<small><i>Tipp:</i> Dieses Fenster an die Taskleiste anheften oder "
            "<b>„IT-KI starten“</b> auf dem Desktop nutzen.</small>"
        )
        hint.set_line_wrap(True)
        vbox.pack_start(hint, False, False, 0)

        shortcut_btn = Gtk.Button(label="📌 Verknüpfung auf Desktop legen")
        shortcut_btn.connect("clicked", self._install_shortcut)
        vbox.pack_start(shortcut_btn, False, False, 0)

        GLib.timeout_add_seconds(90, self._refresh_stats)
        self._refresh_stats()

    def _on_domain_toggled(self, widget, dom_id):
        active_ids = []
        for d_id, chk in self._domain_checks.items():
            if chk.get_active():
                active_ids.append(d_id)
        if not active_ids:
            widget.set_active(True)
            return

        from rag_core.quality import save_active_domains, reload_domains
        save_active_domains(active_ids)
        reload_domains()
        self._refresh_stats()

    def _track(self, win: Gtk.Window):
        self._child_windows.append(win)
        win.connect("destroy", lambda w: self._child_windows.remove(w) if w in self._child_windows else None)

    def _open_chat(self, *_):
        from gui_chat import RAGChatWindow

        w = RAGChatWindow()
        self._track(w)
        w.show_all()

    def _open_collector(self, *_):
        from gui_collector import RAGCollectorGUI

        w = RAGCollectorGUI()
        self._track(w)
        w.show_all()

    def _refresh_stats(self):
        threading.Thread(target=self._stats_worker, daemon=True).start()
        return True

    def _stats_worker(self):
        try:
            stats = gather_stats(data_bytes_hint=self._data_bytes_hint or None)
            markup = format_stats_markup(stats)
        except Exception:
            markup = "<i>Statistik nicht verfügbar</i>"
        GLib.idle_add(self._apply_stats, markup)

    def _apply_stats(self, markup: str):
        try:
            self.stats_label.set_markup(markup)
        except Exception:
            pass
        return False

    def _index_projects(self, *_):
        dlg = ProgressDialog(self, "Projekte indexieren")
        dlg.log("Starte Indexierung von ~/projects …\n")

        def worker():
            try:
                from rag_core.config import INDEX_BATCH_SIZE, USER_WORKSPACE_ROOTS
                from rag_core.indexing import (
                    file_to_user_records,
                    iter_user_project_files,
                    make_splitter,
                    open_prime_table,
                )

                for r in USER_WORKSPACE_ROOTS:
                    GLib.idle_add(dlg.log, f"  → {r}")
                table = open_prime_table(recreate=False)
                splitter = make_splitter()
                files = list(iter_user_project_files())
                GLib.idle_add(dlg.log, f"\n{len(files)} Dateien gefunden.\n")
                batch: list[dict] = []
                done = 0
                for path in files:
                    recs = file_to_user_records(path, splitter)
                    if not recs:
                        continue
                    batch.extend(recs)
                    done += 1
                    if len(batch) >= INDEX_BATCH_SIZE:
                        table.add(batch)
                        batch = []
                        n = table.count_rows()
                        GLib.idle_add(
                            dlg.log, f"  … {done} Dateien | {n:,} Chunks gesamt"
                        )
                if batch:
                    table.add(batch)
                GLib.idle_add(
                    dlg.log,
                    f"\n✅ Fertig: {done} Dateien, {table.count_rows():,} Chunks gesamt.",
                )
                GLib.idle_add(self._refresh_stats)
            except Exception as e:
                GLib.idle_add(dlg.log, f"\n❌ Fehler: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _run_check(self, *_):
        dlg = ProgressDialog(self, "Systemprüfung")

        def worker():
            try:
                result = run_system_check()
                for line in result["lines"]:
                    GLib.idle_add(dlg.log, line)
                if result["ok"]:
                    GLib.idle_add(dlg.log, "\n✅ System bereit.")
                else:
                    GLib.idle_add(dlg.log, "\n⚠️ Einige Prüfungen fehlgeschlagen.")
            except Exception as e:
                GLib.idle_add(dlg.log, f"❌ {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _install_shortcut(self, *_):
        desktop_src = _BASE / "RAG-NVMe.desktop"
        if not desktop_src.exists():
            self._write_desktop_file(desktop_src)
        targets = [
            Path.home() / "Desktop" / "RAG-NVMe.desktop",
            Path.home() / ".local" / "share" / "applications" / "rag-nvme.desktop",
        ]
        msg = []
        for dest in targets:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(desktop_src, dest)
                dest.chmod(0o755)
                msg.append(str(dest))
            except Exception as e:
                msg.append(f"{dest.name}: {e}")
        d = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Verknüpfung erstellt",
        )
        d.format_secondary_text("\n".join(msg))
        d.run()
        d.destroy()

    def _write_desktop_file(self, path: Path):
        exec_path = _BASE / "start-rag.sh"
        path.write_text(
            f"""[Desktop Entry]
Type=Application
Name=RAG NVMe Zentrale
GenericName=Universal-KI-Zentrale
Comment=Allwissende lokale KI-Plattform mit NVMe-Block-Store & Domänen-Manager (100% offline)
Exec={exec_path}
Path={_BASE}
Terminal=false
Categories=Development;Utility;Science;
StartupNotify=true
""",
            encoding="utf-8",
        )
        path.chmod(0o755)


def main():
    win = RAGMainWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
