import { sanitizeHtml } from "./sanitizeHtml";

describe("sanitizeHtml", () => {
  test("removes executable elements and event handlers", () => {
    const html = '<p onclick="alert(1)">Merhaba</p><script>window.pwned=1</script><img src="x" onerror="alert(2)">';
    const clean = sanitizeHtml(html);
    expect(clean).toContain("Merhaba");
    expect(clean).not.toMatch(/script|onclick|onerror/i);
  });

  test("blocks obfuscated executable URLs", () => {
    const clean = sanitizeHtml('<a href="java\tscript:alert(1)">Tıkla</a><img src="data:image/svg+xml,x">');
    expect(clean).not.toMatch(/javascript|data:image\/svg/i);
  });

  test("re-sanitizing a string-sliced fragment neutralises markup that was inside an attribute", () => {
    // ProductDetail açıklamayı "Yıkama" başlığından METİN olarak böler. Eski tarayıcılar
    // öznitelikteki '<' karakterini kaçırmadan serileştirir; kesim öznitelik içine düşerse
    // ikinci yarı yeni bir <img onerror> etiketi olur. Yarılar yeniden temizlenmelidir.
    const oldBrowserSerialised = '<p>Kumaş</p><a title="<b><img src=x onerror=alert(1)>Yıkama">t</a>';
    const cut = oldBrowserSerialised.lastIndexOf("<b", oldBrowserSerialised.search(/Yıkama/));
    const right = sanitizeHtml(oldBrowserSerialised.slice(cut));
    const left = sanitizeHtml(oldBrowserSerialised.slice(0, cut));
    expect(right).not.toMatch(/onerror/i);
    expect(left).not.toMatch(/onerror/i);
    expect(left).toContain("Kumaş");
  });

  test("adds reverse-tabnabbing protection", () => {
    expect(sanitizeHtml('<a href="https://example.com" target="_blank">A</a>'))
      .toContain('rel="noopener noreferrer"');
  });

  test("allows safe email inline styles but drops network-capable styles", () => {
    expect(sanitizeHtml('<p style="color:red">A</p>', { allowStyle: true }))
      .toContain('style="color:red"');
    expect(sanitizeHtml('<p style="background:url(https://evil.test/x)">A</p>', { allowStyle: true }))
      .not.toContain("style=");
  });
});
