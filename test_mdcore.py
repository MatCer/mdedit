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


def test_ordered_list_start_survives_weasyprint():
    # WeasyPrint ignores <ol start>, so the start is also set as a counter-reset.
    html = mdcore.render_body("Intro\n\n24. a\n25. b\n")
    assert '<ol start="24" style="counter-reset: list-item 23">' in html
    assert "<ol>" in mdcore.render_body("1. a\n")


def test_front_matter_becomes_hidden_meta_and_picks_template():
    text = "---\ntemplate: default\nheader: A / <B>\nbad key: x\n---\n# T\n"
    html = mdcore.render_body(text)
    assert '<div class="meta meta-header">A / &lt;B&gt;</div>' in html
    assert "bad key" not in html and "<hr" not in html
    assert mdcore.front_matter(text)["template"] == "default"
    assert mdcore.resolve_template(text) is None
    assert mdcore.front_matter("# no front matter\n") == {}


def test_unknown_or_escaping_template_is_rejected():
    for name in ("nope-not-installed", "../x", "/etc"):
        try:
            mdcore.template_path(name)
        except ValueError:
            continue
        raise AssertionError(name)


def test_meta_strips_one_matching_quote_pair_only():
    meta = mdcore.parse_meta("""a: "x"\nb: Authors'\nc: 'it's'""")
    assert meta == {"a": "x", "b": "Authors'", "c": "it's"}


def test_template_css_makes_relative_urls_absolute():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        css = Path(d) / "style.css"
        css.write_text('a{background:url(logo.svg)} b{background:url("data:x")} c{background:url(https://x/y)}')
        out = mdcore.template_css(css)
    assert f'url("{(Path(d) / "logo.svg").as_uri()}")' in out
    assert 'url("data:x")' in out and "url(https://x/y)" in out


def test_set_template_updates_or_creates_front_matter():
    assert mdcore.set_template("# T\n", "acme", "5 October 2026") == (
        "---\ntemplate: acme\ntitle: \nheader: \nfooter: 5 October 2026\n---\n\n# T\n"
    )
    fm = "---\ntitle: X\ntemplate: old\n---\n# T\n"
    assert mdcore.set_template(fm, "acme", "d") == "---\ntitle: X\ntemplate: acme\n---\n# T\n"
    assert mdcore.set_template("---\ntitle: X\n---\n", "acme", "d") == "---\ntemplate: acme\ntitle: X\n---\n"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
