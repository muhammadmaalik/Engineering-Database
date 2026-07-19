(() => {
  "use strict";

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const moon = document.querySelector(".ascii-moon");
  const moonStage = document.querySelector(".lunar-stage");
  const finalMoon = document.querySelector(".final-moon");
  const header = document.querySelector(".site-header");
  const menuButton = document.querySelector(".menu-button");

  // Deterministic pseudo-random texture keeps the ASCII moon stable between frames.
  const hash = (x, y, seed = 0) => {
    const n = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453;
    return n - Math.floor(n);
  };

  const crater = (x, y, cx, cy, radius) => {
    const d = Math.hypot(x - cx, y - cy);
    if (d > radius) return 0;
    const edge = Math.max(0, 1 - Math.abs(d - radius * .72) / (radius * .28));
    const bowl = Math.max(0, 1 - d / radius);
    return edge * .35 - bowl * .25;
  };

  function renderMoon(phase = 0) {
    if (!moon) return;
    const cols = window.innerWidth < 720 ? 78 : 104;
    const rows = window.innerWidth < 720 ? 50 : 68;
    const chars = "  .,:;irsXA253hMHGS#9B&@";
    const craters = [
      [-.42, -.25, .19], [.25, -.44, .12], [.36, .16, .22],
      [-.18, .32, .13], [.02, -.04, .08], [-.55, .12, .09],
      [.54, -.12, .07], [.12, .55, .11]
    ];
    const lines = [];

    for (let row = 0; row < rows; row++) {
      let line = "";
      const y = (row / (rows - 1)) * 2 - 1;
      for (let col = 0; col < cols; col++) {
        // Characters are taller than wide, so widen normalized x for a round result.
        const x = ((col / (cols - 1)) * 2 - 1) * 1.02;
        const r2 = x * x + y * y;
        if (r2 > .98) {
          line += " ";
          continue;
        }

        const z = Math.sqrt(Math.max(0, 1 - r2));
        const longitude = Math.atan2(x, z) + phase;
        const latitude = Math.asin(y);
        let texture =
          .48 +
          .18 * Math.sin(longitude * 5.2 + Math.cos(latitude * 7)) +
          .11 * Math.sin(longitude * 13.5 - latitude * 8.2) +
          .08 * Math.cos(longitude * 23 + latitude * 17) +
          (hash(Math.floor(longitude * 25), row, 2) - .5) * .22;

        craters.forEach(([cx, cy, radius], index) => {
          const projectedX = Math.sin(Math.atan2(cx, Math.sqrt(Math.max(0, 1 - cx * cx))) + phase);
          texture += crater(x, y, projectedX, cy, radius) * (index % 2 ? .8 : 1);
        });

        const light = Math.max(0, x * -.42 + y * -.2 + z * .92);
        const rim = Math.pow(z, .25);
        const value = Math.max(0, Math.min(1, texture * .58 + light * .55)) * rim;
        line += chars[Math.min(chars.length - 1, Math.floor(value * chars.length))];
      }
      lines.push(line);
    }
    moon.textContent = lines.join("\n");
  }

  // Starfield: deliberately sparse, monochrome, and subtle.
  const canvas = document.querySelector("#starfield");
  const ctx = canvas?.getContext("2d");
  let stars = [];
  let dpr = Math.min(window.devicePixelRatio || 1, 2);

  function resizeStars() {
    if (!canvas || !ctx) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(window.innerWidth * dpr);
    canvas.height = Math.floor(window.innerHeight * dpr);
    canvas.style.width = `${window.innerWidth}px`;
    canvas.style.height = `${window.innerHeight}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.floor((window.innerWidth * window.innerHeight) / 11500);
    stars = Array.from({ length: count }, (_, i) => ({
      x: hash(i, 2) * window.innerWidth,
      y: hash(i, 7) * window.innerHeight,
      r: .25 + hash(i, 11) * 1.1,
      a: .15 + hash(i, 19) * .5,
      speed: .1 + hash(i, 23) * .35
    }));
  }

  let starTime = 0;
  function drawStars() {
    if (!ctx) return;
    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    stars.forEach((star, i) => {
      const pulse = reducedMotion ? 1 : .75 + Math.sin(starTime * star.speed + i) * .25;
      ctx.beginPath();
      ctx.fillStyle = `rgba(235,235,228,${star.a * pulse})`;
      ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
      ctx.fill();
    });
    starTime += .012;
    if (!reducedMotion) requestAnimationFrame(drawStars);
  }

  let ticking = false;
  function updateScrollEffects() {
    const scrollY = window.scrollY;

    if (!reducedMotion) {
      const heroRatio = Math.min(1, scrollY / Math.max(1, window.innerHeight));
      const phase = heroRatio * Math.PI * 1.3;
      renderMoon(phase);
      if (moon) {
        moon.style.transform = `rotate(${heroRatio * 14 - 7}deg) rotateY(${heroRatio * 32}deg)`;
      }
      if (moonStage) {
        moonStage.style.translate = `0 ${scrollY * .075}px`;
      }
      if (finalMoon) {
        const rect = finalMoon.parentElement.getBoundingClientRect();
        const local = (window.innerHeight - rect.top) / (window.innerHeight + rect.height);
        finalMoon.style.rotate = `${(local - .5) * 18}deg`;
        finalMoon.style.scale = `${.94 + Math.max(0, Math.min(1, local)) * .08}`;
      }
    }
    ticking = false;
  }

  window.addEventListener("scroll", () => {
    if (!ticking) {
      requestAnimationFrame(updateScrollEffects);
      ticking = true;
    }
  }, { passive: true });

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      }
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: .08 });
  document.querySelectorAll(".reveal").forEach((element, index) => {
    element.style.transitionDelay = `${Math.min(index % 5, 4) * 70}ms`;
    observer.observe(element);
  });

  document.querySelectorAll(".copy-button").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy || "");
        const old = button.textContent;
        button.textContent = "COPIED";
        setTimeout(() => { button.textContent = old; }, 1400);
      } catch {
        button.textContent = "SELECT CODE";
      }
    });
  });

  document.querySelectorAll(".faq-list details").forEach((detail) => {
    detail.addEventListener("toggle", () => {
      if (!detail.open) return;
      document.querySelectorAll(".faq-list details[open]").forEach((other) => {
        if (other !== detail) other.open = false;
      });
    });
  });

  if (menuButton && header) {
    menuButton.addEventListener("click", () => {
      const open = header.classList.toggle("open");
      menuButton.setAttribute("aria-expanded", String(open));
    });
    header.querySelectorAll("nav a").forEach((link) => {
      link.addEventListener("click", () => {
        header.classList.remove("open");
        menuButton.setAttribute("aria-expanded", "false");
      });
    });
  }

  const marquee = document.querySelector(".stack-marquee div");
  if (marquee) marquee.innerHTML += marquee.innerHTML;

  window.addEventListener("resize", () => {
    resizeStars();
    renderMoon(window.scrollY / Math.max(1, window.innerHeight) * Math.PI);
  });

  renderMoon(0);
  resizeStars();
  drawStars();
  updateScrollEffects();
})();
