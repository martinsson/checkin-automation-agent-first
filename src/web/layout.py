"""
Shared HTML shell + design system for the owner-facing pages (login, door
codes, ...). One place for the CSS and the card/brand chrome so the pages stay
consistent without each one re-declaring styles.
"""

# The whole design system in one stylesheet. Plain string (not an f-string), so
# CSS braces are written normally.
_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: linear-gradient(135deg, #1e3a5f 0%, #2d6a4f 100%);
  color: #1a1a1a;
  padding: 1.5rem;
}
/* margin:auto centers the card on both axes, and (unlike align-items:center)
   lets a taller-than-viewport list still scroll to the top. */
.card {
  background: #fff;
  width: 100%;
  margin: auto;
  padding: 2.25rem 2rem;
  border-radius: 14px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
}
.brand { text-align: center; margin-bottom: 1.75rem; }
.brand .logo { font-size: 2.25rem; line-height: 1; }
.brand h1 { font-size: 1.15rem; margin: 0.5rem 0 0.15rem; font-weight: 600; }
.brand p { margin: 0; color: #6b7280; font-size: 0.85rem; }
label {
  display: block;
  font-size: 0.8rem;
  font-weight: 600;
  color: #374151;
  margin-bottom: 0.35rem;
}
input, select {
  width: 100%;
  padding: 0.7rem 0.8rem;
  margin-bottom: 1.1rem;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 1rem;
  background: #fff;
  transition: border-color 0.15s, box-shadow 0.15s;
}
input:focus, select:focus {
  outline: none;
  border-color: #2d6a4f;
  box-shadow: 0 0 0 3px rgba(45, 106, 79, 0.15);
}
button {
  width: 100%;
  padding: 0.75rem;
  border: none;
  border-radius: 8px;
  background: #2d6a4f;
  color: #fff;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}
button:hover { background: #245a41; }
.error {
  background: #fdecea; color: #b3261e; border-left: 3px solid #f44336;
  padding: 0.7rem 0.8rem; border-radius: 6px; font-size: 0.88rem; margin: 0 0 1.1rem;
}
.hint { color: #6b7280; font-size: 0.82rem; margin: -0.5rem 0 1.1rem; }
.success {
  background: #e8f5e9; border-left: 3px solid #4caf50; border-radius: 8px;
  text-align: center; padding: 1.2rem; margin-bottom: 1.25rem;
}
.success .code {
  font-size: 2.4rem; letter-spacing: 0.12em; font-weight: 700; color: #1b5e20;
}
.meta { color: #374151; font-size: 0.92rem; line-height: 1.55; margin: 0; }
.links { margin: 1.6rem 0 0; text-align: center; font-size: 0.85rem; }
.links a { color: #2d6a4f; text-decoration: none; font-weight: 600; }
.links a:hover { text-decoration: underline; }

/* Inline button variants (the default button is a full-width primary action). */
button.inline { width: auto; display: inline-block; padding: 0.5rem 1rem; font-size: 0.9rem; }
button.danger { background: #c0392b; }
button.danger:hover { background: #a93226; }

/* Review list — draft cards and their context boxes. */
.empty { color: #6b7280; text-align: center; margin: 2rem 0; }
.draft { border: 1px solid #e5e7eb; border-radius: 10px; padding: 1.25rem; margin-bottom: 1.25rem; }
.draft h3 { margin: 0 0 0.15rem; font-size: 1rem; }
.draft .when { color: #6b7280; font-size: 0.8rem; }
.draft h4 {
  margin: 1.1rem 0 0.4rem; font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.05em; color: #6b7280;
}
.ctx {
  padding: 0.7rem 0.85rem; margin-bottom: 0.5rem; border-radius: 6px;
  border-left: 3px solid #cbd5e1; background: #f8fafc; font-size: 0.9rem;
}
.ctx--guest { background: #e8f4fd; border-left-color: #2196f3; }
.ctx--out { background: #fff3e0; border-left-color: #ff9800; }
.ctx--reply { background: #e8f5e9; border-left-color: #4caf50; }
.ctx pre, .reply { white-space: pre-wrap; font-family: ui-monospace, Menlo, monospace; font-size: 0.85rem; }
.ctx pre { margin: 0.3rem 0 0; }
.reply { background: #f5f5f7; padding: 0.85rem; border-radius: 8px; margin: 0; }
.actions { margin-top: 1rem; display: flex; gap: 0.6rem; align-items: center; flex-wrap: wrap; }
.actions form { margin: 0; display: flex; gap: 0.6rem; align-items: center; }
.actions input { margin: 0; width: auto; min-width: 200px; }

/* Tap-to-copy button (e.g. the created door code). */
button.copy {
  width: 100%; margin-top: 0.9rem; background: #fff; color: #1b5e20;
  border: 1px solid #4caf50; font-size: 0.95rem;
}
button.copy:hover { background: #f1f8f2; }
button.copy.done { background: #4caf50; color: #fff; border-color: #4caf50; }

/* Mobile-first: this console is used mostly on phones. */
@media (max-width: 480px) {
  body { padding: 0.75rem; }
  .card { padding: 1.5rem 1.25rem; border-radius: 12px; }
  .success .code { font-size: 2rem; }
  .actions { flex-direction: column; align-items: stretch; }
  .actions form { width: 100%; }
  .actions form button, .actions input { width: 100%; min-width: 0; }
}
"""


def brand(*, logo: str, heading: str, subtitle: str = "") -> str:
    """The centered emoji + title header at the top of a card."""
    sub = f"<p>{subtitle}</p>" if subtitle else ""
    return f'<div class="brand"><div class="logo">{logo}</div><h1>{heading}</h1>{sub}</div>'


def page(*, title: str, content: str, max_width: str = "420px") -> str:
    """Wrap card content in the full HTML document with the shared design system."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>{_CSS}
    .card {{ max-width: {max_width}; }}
  </style>
</head>
<body>
  <main class="card">
{content}
  </main>
</body>
</html>
"""
