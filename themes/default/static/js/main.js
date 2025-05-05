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
