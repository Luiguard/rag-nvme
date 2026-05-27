import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

def apply_theme():
    css = b"""
    @keyframes pulse {
        0%   { opacity: 0.6; }
        50%  { opacity: 1.0; }
        100% { opacity: 0.6; }
    }

    window {
        background-color: #0f0f17;
    }

    .header-bar {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border-bottom: 1px solid #2a2a4a;
        padding: 12px 16px;
        border-radius: 0;
    }

    .title-label {
        color: #e2e8f0;
        font-size: 15pt;
        font-weight: 800;
        letter-spacing: 0.5px;
    }

    .subtitle-label {
        color: #64748b;
        font-size: 9pt;
        font-weight: 400;
    }

    .status-bar {
        background-color: #131320;
        border-bottom: 1px solid #1e1e35;
        padding: 6px 14px;
    }

    .status-label {
        color: #94a3b8;
        font-size: 9pt;
        font-family: monospace;
    }

    .chat-scroll {
        background-color: transparent;
        border: none;
    }

    .chat-scroll scrollbar slider {
        background-color: #334155;
        border-radius: 6px;
        min-width: 6px;
    }

    .chat-scroll scrollbar slider:hover {
        background-color: #475569;
    }

    .chat-scroll scrollbar trough {
        background-color: transparent;
    }

    .chat-view text {
        background-color: #0f0f17;
        color: #cbd5e1;
        font-family: 'JetBrains Mono', 'Fira Code', 'Source Code Pro', monospace;
        font-size: 10.5pt;
        padding: 8px;
    }

    .chat-view text selection {
        background-color: #3b82f6;
        color: #ffffff;
    }

    .toolbar {
        background-color: #131320;
        border-top: 1px solid #1e1e35;
        padding: 8px 12px;
    }

    .combo-styled {
        background-color: #1e1e35;
        color: #94a3b8;
        border: 1px solid #2a2a4a;
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 9pt;
        font-weight: 500;
        min-height: 28px;
    }

    .combo-styled:hover {
        border-color: #3b82f6;
        background-color: #1e2444;
    }

    .input-area {
        background-color: #0c0c14;
        border-top: 1px solid #1e1e35;
        padding: 10px 14px;
    }

    .input-frame {
        background-color: #161626;
        border: 1px solid #2a2a4a;
        border-radius: 12px;
        padding: 2px;
    }

    .input-frame:focus-within {
        border-color: #3b82f6;
        box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15);
    }

    .input-text text {
        background-color: #161626;
        color: #e2e8f0;
        font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
        font-size: 11pt;
        padding: 8px 12px;
        caret-color: #3b82f6;
    }

    .input-text text selection {
        background-color: #3b82f6;
        color: #ffffff;
    }

    .input-text:focus text {
        background-color: #161626;
    }

    button {
        background-color: #1e1e35;
        color: #94a3b8;
        border: 1px solid #2a2a4a;
        border-radius: 10px;
        padding: 8px 14px;
        font-weight: 600;
        font-size: 10pt;
        transition: all 150ms ease;
        min-height: 20px;
    }

    button:hover {
        background-color: #2a2a4a;
        border-color: #3b4a6b;
        color: #e2e8f0;
    }

    button.suggested-action {
        background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%);
        color: #ffffff;
        border: none;
        font-weight: 700;
        padding: 8px 20px;
        text-shadow: 0 1px 2px rgba(0,0,0,0.2);
    }

    button.suggested-action:hover {
        background: linear-gradient(135deg, #60a5fa 0%, #818cf8 100%);
    }

    button.suggested-action:active {
        background: linear-gradient(135deg, #2563eb 0%, #4f46e5 100%);
    }

    button.destructive-action {
        background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
        color: #ffffff;
        border: none;
        font-weight: 700;
    }

    button.destructive-action:hover {
        background: linear-gradient(135deg, #f87171 0%, #ef4444 100%);
    }

    .btn-icon {
        background-color: transparent;
        border: 1px solid #2a2a4a;
        border-radius: 10px;
        padding: 6px 10px;
        min-width: 36px;
        min-height: 36px;
    }

    .btn-icon:hover {
        background-color: #1e1e35;
        border-color: #3b82f6;
    }

    .btn-icon.active {
        background-color: rgba(59, 130, 246, 0.15);
        border-color: #3b82f6;
        color: #60a5fa;
    }

    .btn-stop {
        background: linear-gradient(135deg, #f59e0b 0%, #ef4444 100%);
        color: #ffffff;
        border: none;
        border-radius: 10px;
        padding: 8px 18px;
        font-weight: 700;
        font-size: 10pt;
        animation: pulse 1.5s ease-in-out infinite;
    }

    .btn-stop:hover {
        background: linear-gradient(135deg, #fbbf24 0%, #f87171 100%);
    }

    .separator {
        background-color: #1e1e35;
        min-height: 1px;
    }

    label {
        color: #94a3b8;
        font-size: 10pt;
    }

    .label-accent {
        color: #60a5fa;
        font-weight: 600;
    }
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
