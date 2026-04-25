console.log('Railway Manager MVP loaded');

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-projects-carousel]').forEach((carousel) => {
    const track = carousel.querySelector('.projects-carousel-track');
    if (!track || !track.children.length) return;

    let isDown = false;
    let startX = 0;
    let startScrollLeft = 0;
    let pausedUntil = 0;
    let lastFrame = performance.now();
    const speed = 42;
    const copies = Math.max(Number(track.dataset.loopCopies || 8), 2);

    const singleLoopWidth = () => track.scrollWidth / copies;

    const normalizeScroll = () => {
      const loopWidth = singleLoopWidth();
      if (!loopWidth) return;

      const minSafeScroll = loopWidth * 2;
      const maxSafeScroll = loopWidth * 4;

      if (carousel.scrollLeft >= maxSafeScroll) {
        carousel.scrollLeft -= loopWidth;
      } else if (carousel.scrollLeft <= minSafeScroll - loopWidth) {
        carousel.scrollLeft += loopWidth;
      }
    };

    const pauseBriefly = (duration = 1200) => {
      pausedUntil = performance.now() + duration;
    };

    carousel.addEventListener('pointerdown', (event) => {
      isDown = true;
      carousel.classList.add('dragging');
      startX = event.clientX;
      startScrollLeft = carousel.scrollLeft;
      pauseBriefly(2200);
      carousel.setPointerCapture?.(event.pointerId);
    });

    carousel.addEventListener('pointermove', (event) => {
      if (!isDown) return;
      const delta = event.clientX - startX;
      carousel.scrollLeft = startScrollLeft - delta;
      normalizeScroll();
    });

    const stopDragging = (event) => {
      if (!isDown) return;
      isDown = false;
      carousel.classList.remove('dragging');
      pauseBriefly(900);
      carousel.releasePointerCapture?.(event.pointerId);
    };

    carousel.addEventListener('pointerup', stopDragging);
    carousel.addEventListener('pointercancel', stopDragging);
    carousel.addEventListener('mouseleave', () => {
      if (isDown) carousel.classList.remove('dragging');
      isDown = false;
    });
    carousel.addEventListener('wheel', () => {
      pauseBriefly(1000);
      requestAnimationFrame(normalizeScroll);
    }, { passive: true });

    const autoplay = (now) => {
      const elapsed = Math.min((now - lastFrame) / 1000, 0.08);
      lastFrame = now;

      if (!isDown && now > pausedUntil) {
        carousel.scrollLeft += speed * elapsed;
        normalizeScroll();
      }

      requestAnimationFrame(autoplay);
    };

    requestAnimationFrame(() => {
      carousel.scrollLeft = singleLoopWidth() * 3;
      lastFrame = performance.now();
      requestAnimationFrame(autoplay);
    });
  });
});
