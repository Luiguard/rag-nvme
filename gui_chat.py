"""GTK-Chat – lokaler KI-Assistent mit Abbrechen, editierbarem Prompt und Premium-UI."""
from __future__ import annotations

import os
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

from rag_core.gui_resources import child_env

_env = child_env()
for _k, _v in _env.items():
    if _k.startswith(("OMP_", "MKL_", "NUMEXPR_", "OPENBLAS_", "VECLIB_", "TOKENIZERS")):
        os.environ[_k] = _v

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk, Pango

from rag_core.assistant import LocalAssistant
from rag_core.embeddings import embed_query
from rag_core.ollama import resolve_model
from rag_core.prompts import MODE_AUDIT, MODE_CODE, MODE_REVIEW, MODE_SUPPORT
from rag_core.retrieval import KnowledgeRetriever

MODE_LABELS = {
    "auto": "⚡ Auto",
    MODE_SUPPORT: "💬 Support",
    MODE_CODE: "🖥 Code",
    MODE_REVIEW: "🔍 Review",
    MODE_AUDIT: "🛡 Audit",
}


class RAGChatWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Lokale KI – Assistent")
        self.set_default_size(860, 680)
        self.set_border_width(0)
        self._busy = False
        self._cancel_requested = False
        self._assistant: LocalAssistant | None = None
        self._retriever: KnowledgeRetriever | None = None
        self._attached_context = ""
        self._attached_name = ""

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        # ── Header ──
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.get_style_context().add_class("header-bar")

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="🧠  Lokale KI")
        title.get_style_context().add_class("title-label")
        title.set_halign(Gtk.Align.START)
        subtitle = Gtk.Label(label="100% offline · RAG-gestützt · sicher")
        subtitle.get_style_context().add_class("subtitle-label")
        subtitle.set_halign(Gtk.Align.START)
        title_box.pack_start(title, False, False, 0)
        title_box.pack_start(subtitle, False, False, 0)
        header.pack_start(title_box, True, True, 0)

        root.pack_start(header, False, False, 0)

        # ── Status Bar ──
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_box.get_style_context().add_class("status-bar")
        self.status_label = Gtk.Label(label="⏳ Initialisiere…")
        self.status_label.get_style_context().add_class("status-label")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        status_box.pack_start(self.status_label, True, True, 4)
        root.pack_start(status_box, False, False, 0)

        # ── Chat Area ──
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.get_style_context().add_class("chat-scroll")

        self.chat_view = Gtk.TextView()
        self.chat_view.set_editable(False)
        self.chat_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.chat_view.set_cursor_visible(False)
        self.chat_view.set_left_margin(16)
        self.chat_view.set_right_margin(16)
        self.chat_view.set_top_margin(12)
        self.chat_view.set_bottom_margin(12)
        self.chat_view.get_style_context().add_class("chat-view")
        scroll.add(self.chat_view)
        root.pack_start(scroll, True, True, 0)

        self._buf = self.chat_view.get_buffer()
        self._create_text_tags()
        self._append_styled("system", "Willkommen. Stelle eine Frage oder wähle einen Modus.\n")

        # ── Toolbar (DB, Model, Mode) ──
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.get_style_context().add_class("toolbar")

        self.db_combo = Gtk.ComboBoxText()
        self.db_combo.get_style_context().add_class("combo-styled")
        db_dir = _BASE / "lancedb_index"
        has_dbs = False
        if db_dir.exists():
            for d in sorted(db_dir.glob("*.lance")):
                if d.is_dir():
                    self.db_combo.append(d.stem, f"📚 {d.stem}")
                    has_dbs = True
        if not has_dbs:
            self.db_combo.append("it_prime", "📚 it_prime")
        self.db_combo.set_active(0)
        self.db_combo.connect("changed", self._on_db_changed)
        toolbar.pack_start(self.db_combo, False, False, 0)

        self.model_combo = Gtk.ComboBoxText()
        self.model_combo.get_style_context().add_class("combo-styled")
        self.model_combo.set_tooltip_text("KI-Modell auswählen")
        self._populate_model_combo()
        toolbar.pack_start(self.model_combo, True, True, 0)

        self.mode_combo = Gtk.ComboBoxText()
        self.mode_combo.get_style_context().add_class("combo-styled")
        for key, label in MODE_LABELS.items():
            self.mode_combo.append(key, label)
        self.mode_combo.set_active(0)
        self.mode_combo.set_tooltip_text("Antwort-Stil")
        toolbar.pack_start(self.mode_combo, False, False, 0)

        root.pack_start(toolbar, False, False, 0)

        # ── Input Area ──
        input_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        input_area.get_style_context().add_class("input-area")

        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.btn_attach = Gtk.Button(label="📎")
        self.btn_attach.get_style_context().add_class("btn-icon")
        self.btn_attach.set_tooltip_text("Datei anhängen")
        self.btn_attach.connect("clicked", self._on_attach_clicked)
        input_row.pack_start(self.btn_attach, False, False, 0)

        input_frame = Gtk.Frame()
        input_frame.set_shadow_type(Gtk.ShadowType.NONE)
        input_frame.get_style_context().add_class("input-frame")

        input_scroll = Gtk.ScrolledWindow()
        input_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        input_scroll.set_min_content_height(40)
        input_scroll.set_max_content_height(120)

        self.input_view = Gtk.TextView()
        self.input_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.input_view.set_left_margin(8)
        self.input_view.set_right_margin(8)
        self.input_view.set_top_margin(6)
        self.input_view.set_bottom_margin(6)
        self.input_view.get_style_context().add_class("input-text")
        self.input_view.connect("key-press-event", self._on_input_key)
        input_scroll.add(self.input_view)
        input_frame.add(input_scroll)
        input_row.pack_start(input_frame, True, True, 0)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.btn_send = Gtk.Button(label="Senden ⏎")
        self.btn_send.get_style_context().add_class("suggested-action")
        self.btn_send.set_tooltip_text("Nachricht senden (Enter)")
        self.btn_send.connect("clicked", self._on_send)
        btn_box.pack_start(self.btn_send, True, True, 0)

        self.btn_stop = Gtk.Button(label="⏹ Stop")
        self.btn_stop.get_style_context().add_class("btn-stop")
        self.btn_stop.set_tooltip_text("KI-Antwort abbrechen")
        self.btn_stop.connect("clicked", self._on_stop)
        self.btn_stop.set_no_show_all(True)
        self.btn_stop.hide()
        btn_box.pack_start(self.btn_stop, True, True, 0)

        input_row.pack_start(btn_box, False, False, 0)
        input_area.pack_start(input_row, False, False, 0)

        # Anhang-Anzeige
        self.attach_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.attach_bar.set_no_show_all(True)
        self.attach_bar.hide()
        self.attach_label = Gtk.Label()
        self.attach_label.get_style_context().add_class("label-accent")
        self.attach_label.set_halign(Gtk.Align.START)
        self.attach_bar.pack_start(self.attach_label, True, True, 0)
        btn_remove = Gtk.Button(label="✕")
        btn_remove.get_style_context().add_class("btn-icon")
        btn_remove.set_tooltip_text("Anhang entfernen")
        btn_remove.connect("clicked", self._on_remove_attach)
        self.attach_bar.pack_start(btn_remove, False, False, 0)
        input_area.pack_start(self.attach_bar, False, False, 4)

        root.pack_start(input_area, False, False, 0)

        threading.Thread(target=self._init_worker, daemon=True).start()

    def _create_text_tags(self):
        self._buf.create_tag("role-user", foreground="#60a5fa", weight=Pango.Weight.BOLD, pixels_above_lines=12)
        self._buf.create_tag("role-ki", foreground="#a78bfa", weight=Pango.Weight.BOLD, pixels_above_lines=12)
        self._buf.create_tag("role-system", foreground="#64748b", style=Pango.Style.ITALIC, pixels_above_lines=8)
        self._buf.create_tag("role-sources", foreground="#94a3b8", scale=0.88, pixels_above_lines=4)
        self._buf.create_tag("role-error", foreground="#f87171", weight=Pango.Weight.BOLD)
        self._buf.create_tag("body", foreground="#cbd5e1", pixels_below_lines=4)
        self._buf.create_tag("separator", foreground="#334155", scale=0.8)

    def _append_styled(self, role: str, text: str):
        end = self._buf.get_end_iter()
        tag_map = {
            "user": ("role-user", "  Du"),
            "ki": ("role-ki", "  KI"),
            "system": ("role-system", "  System"),
            "sources": ("role-sources", "  Quellen"),
            "error": ("role-error", "  Fehler"),
        }
        tag_name, label = tag_map.get(role, ("role-system", f"  {role}"))

        self._buf.insert_with_tags_by_name(end, f"\n{label}\n", tag_name)
        end = self._buf.get_end_iter()
        self._buf.insert_with_tags_by_name(end, text + "\n", "body")
        self._auto_scroll()

    def _append_stream(self, text: str):
        end = self._buf.get_end_iter()
        self._buf.insert_with_tags_by_name(end, text, "body")
        self._auto_scroll()

    def _auto_scroll(self):
        end = self._buf.get_end_iter()
        mark = self._buf.create_mark(None, end, False)
        self.chat_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        self._buf.delete_mark(mark)

    def _populate_model_combo(self):
        self.model_combo.remove_all()
        try:
            from rag_core.ollama import list_models
            available = list_models()
        except Exception:
            available = []

        recommended = {
            "llama3.2:1b": "Laptop · sehr schnell",
            "llama3.2:3b": "Standard · gute Balance",
            "qwen2.5-coder:7b": "Code · guter PC",
            "mistral:7b": "Allrounder · guter PC",
            "qwen2.5-coder:14b": "Stark · Mac M-Serie",
            "mixtral:8x7b": "Experte · Workstation",
            "qwen2.5-coder:32b": "Mastermind · 24GB+ RAM",
        }

        for m in available:
            label = recommended.get(m, m)
            self.model_combo.append(m, f"✅ {m}  ({label})" if m in recommended else f"✅ {m}")

        for m, label in recommended.items():
            if m not in available:
                self.model_combo.append(m, f"📥 {m}  ({label})")

        if available:
            self.model_combo.set_active_id(available[0])
        else:
            self.model_combo.set_active(0)

    def _on_db_changed(self, combo):
        if self._busy:
            return
        db_name = combo.get_active_id()
        if db_name:
            self.status_label.set_text(f"⏳ Lade {db_name}…")
            threading.Thread(target=self._init_worker, args=(db_name,), daemon=True).start()

    def _init_worker(self, table_name=None):
        try:
            if not table_name:
                table_name = GLib.idle_add(lambda: self.db_combo.get_active_id()) or None
                import time; time.sleep(0.1)
                table_name = None
            r = KnowledgeRetriever(table_name=table_name)
            a = LocalAssistant(r)
            rows = r.row_count() if r.ready else 0
            model = resolve_model() or "nicht verbunden"
            status = (
                f"✅ {rows:,} Chunks · {r.table_name} · Ollama: {model}"
                if r.ready
                else f"⚠️ Keine Daten in {r.table_name}"
            )
            GLib.idle_add(self._on_ready, a, status)
        except Exception as e:
            GLib.idle_add(self._on_ready, None, f"❌ {e}")

    def _on_ready(self, assistant, status):
        self._assistant = assistant
        if assistant:
            self._retriever = assistant.retriever
        self.status_label.set_text(status)
        return False

    def _on_attach_clicked(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Datei anhängen", parent=self, action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    self._attached_context = f.read()
                self._attached_name = os.path.basename(path)
                self.attach_label.set_text(f"📎 {self._attached_name}")
                self.attach_bar.show()
                self.btn_attach.get_style_context().add_class("active")
            except Exception as e:
                self._append_styled("error", f"Datei nicht lesbar: {e}")
        dialog.destroy()

    def _on_remove_attach(self, *_):
        self._attached_context = ""
        self._attached_name = ""
        self.attach_bar.hide()
        self.btn_attach.get_style_context().remove_class("active")

    def _get_input_text(self) -> str:
        buf = self.input_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()

    def _clear_input(self):
        self.input_view.get_buffer().set_text("")

    def _on_input_key(self, widget, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not (event.state & Gdk.ModifierType.SHIFT_MASK):
                self._on_send()
                return True
        return False

    def _on_send(self, *_args):
        if self._busy or not self._assistant:
            return
        text = self._get_input_text()
        if not text:
            return
        self._clear_input()
        self._busy = True
        self._cancel_requested = False
        self._set_ui_busy(True)
        self._append_styled("user", text)
        threading.Thread(target=self._answer_worker, args=(text,), daemon=True).start()

    def _on_stop(self, *_args):
        self._cancel_requested = True
        self._append_stream("\n\n⏹ Abgebrochen.")
        self._done_answering()

    def _set_ui_busy(self, busy: bool):
        self.btn_send.set_visible(not busy)
        self.btn_stop.set_visible(busy)
        if busy:
            self.btn_stop.show()
        else:
            self.btn_stop.hide()
        self.db_combo.set_sensitive(not busy)
        self.model_combo.set_sensitive(not busy)
        self.mode_combo.set_sensitive(not busy)

    def _selected_mode(self):
        key = self.mode_combo.get_active_id()
        return None if key == "auto" or not key else key

    def _answer_worker(self, query: str):
        mode = self._selected_mode()
        model_id = self.model_combo.get_active_id()
        if not model_id:
            text = self.model_combo.get_active_text() or ""
            model_id = text.replace("✅ ", "").replace("📥 ", "").split("  (")[0].strip()

        try:
            from rag_core.ollama import list_models, pull_model
            if model_id not in list_models():
                GLib.idle_add(self._append_styled, "system", f"⬇️ Lade '{model_id}'…")
                pull_model(model_id)
                GLib.idle_add(self._append_styled, "system", f"✅ '{model_id}' bereit!")
        except Exception as e:
            GLib.idle_add(self._append_styled, "error", f"Modell-Download: {e}")
            GLib.idle_add(self._done_answering)
            return

        if self._cancel_requested:
            return

        try:
            _, mobile = embed_query(query)
            q, parsed_mode = self._assistant.parse_mode(query)
            use_mode = mode or parsed_mode

            def on_status(msg: str):
                GLib.idle_add(self.status_label.set_text, msg)

            if self._cancel_requested:
                return

            GLib.idle_add(self._start_ki_block)

            def on_token(chunk: str):
                if self._cancel_requested:
                    raise InterruptedError("Abbruch durch Benutzer")
                GLib.idle_add(self._append_stream, chunk)

            ctx = f"[Datei: {self._attached_name}]\n{self._attached_context}" if self._attached_context else ""

            result = self._assistant.answer_text(
                q, mode=use_mode, model=model_id, user_context=ctx,
                on_token=on_token, on_status=on_status, retry_on_unsafe=True,
            )

            hits = result.get("hits", [])
            summary = self._retriever.format_hit_summary(hits, mobile=mobile)
            GLib.idle_add(self._append_styled, "sources", summary or "(keine)")

            if result.get("blocked"):
                GLib.idle_add(self._append_styled, "system", result["text"])
        except InterruptedError:
            pass
        except Exception as e:
            if not self._cancel_requested:
                GLib.idle_add(self._append_styled, "error", str(e))
        finally:
            GLib.idle_add(self._done_answering)

    def _start_ki_block(self):
        end = self._buf.get_end_iter()
        self._buf.insert_with_tags_by_name(end, "\n  KI\n", "role-ki")
        return False

    def _done_answering(self):
        self._busy = False
        self._cancel_requested = False
        self._set_ui_busy(False)
        self.input_view.grab_focus()
        return False


if __name__ == "__main__":
    from rag_core.gui_theme import apply_theme
    apply_theme()
    win = RAGChatWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
