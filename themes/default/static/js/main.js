document.addEventListener("DOMContentLoaded", () => {
  const loader = document.querySelector(".site-nav-loader");
  let fakeProgress = 0;
  let loaded = false;

  const step = () => {
    if (loaded) return;

    fakeProgress += Math.random() * 5;
    loader.style.width = Math.min(fakeProgress, 90) + "%";

    requestAnimationFrame(step);
  };

  step();

  window.addEventListener("load", () => {
    loaded = true;
    loader.style.width = "100%";

    // 一瞬白く → 元の色に戻す
    const originalColor = getComputedStyle(loader).backgroundColor;

    loader.style.transition = "background-color 0.6s ease";
    loader.style.backgroundColor = "#ffffff";

    setTimeout(() => {
      loader.style.backgroundColor = originalColor;
    }, 300);
  });
});

document.addEventListener("DOMContentLoaded", function () {
  const clampText = (el, lineCount = 3) => {
    const computed = window.getComputedStyle(el);
    let lineHeight = parseFloat(computed.lineHeight);

    if (isNaN(lineHeight)) lineHeight = 24; // デフォルト

    const maxHeight = lineHeight * lineCount;
    const originalText = el.dataset.originalText || el.innerText.trim();
    const chars = originalText.split("");

    // 元のテキスト保存（リサイズ用）
    el.dataset.originalText = originalText;

    let low = 0;
    let high = chars.length;
    let best = "";

    while (low <= high) {
      const mid = Math.floor((low + high) / 2);
      el.innerText = chars.slice(0, mid).join("") + "…";

      if (el.scrollHeight > maxHeight + 1) {
        high = mid - 1;
      } else {
        best = chars.slice(0, mid).join("") + "…";
        low = mid + 1;
      }
    }

    el.innerText = best;
  };

  const updateClamps = () => {
    document.querySelectorAll(".post-list-item-preview-summary-text").forEach((el) => {
      clampText(el, 5);
    });
  };

  window.addEventListener("load", updateClamps);
  window.addEventListener("resize", () => {
    // 横幅が変わったときに再実行
    updateClamps();
  });
});
