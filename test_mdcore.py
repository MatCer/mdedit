"""Regression checks for the renderer. Run: python test_mdcore.py"""

import mdcore


def test_lists_without_blank_line_and_two_space_nesting():
    html = mdcore.render_body("Intro:\n- one\n- two\n  - nested\n")
    assert "<p>Intro:</p>" in html
    assert html.count("<ul>") == 2
    assert "<li>nested</li>" in html


def test_mermaid_block_and_sources():
    text = "```mermaid\ngraph TD; A-->B\n```\n\n```py\nx = 1\n```\n"
    html = mdcore.render_body(text)
    assert '<pre class="mermaid">graph TD; A--&gt;B\n</pre>' in html
    assert mdcore.mermaid_sources(text) == ["graph TD; A-->B\n"]


def test_svgs_replace_fences_only_and_failed_stay_source():
    # A hand-written <pre class="mermaid"> must not consume a fence's SVG.
    text = '<pre class="mermaid">raw</pre>\n\n```mermaid\na\n```\n\n```mermaid\nb\n```\n'
    svg = '<svg width="100%" style="max-width: 120px;" viewBox="0 0 120 40"></svg>'
    out = mdcore.render_body(text, [svg, None])
    assert out.count('<figure class="mermaid">') == 1
    assert '<pre class="mermaid">raw</pre>' in out
    assert '<pre class="mermaid">b\n</pre>' in out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
