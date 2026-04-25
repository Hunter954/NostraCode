console.log('Railway Manager MVP loaded');

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-projects-carousel]').forEach((carousel) => {
    const track = carousel.querySelector('.projects-carousel-track');
    if (!track) return;

    let isDown = false;
    let startX = 0;
    let startScrollLeft = 0;
    let pausedUntil = 0;
    let lastFrame = performance.now();
    const speed = 34;

    const halfWidth = () => track.scrollWidth / 2;

    const normalizeScroll = () => {
      const loopPoint = halfWidth();
      if (!loopPoint) return;
      if (carousel.scrollLeft >= loopPoint) carousel.scrollLeft -= loopPoint;
      if (carousel.scrollLeft <= 0) carousel.scrollLeft += loopPoint;
    };

    const pauseBriefly = (duration = 1600) => {
      pausedUntil = performance.now() + duration;
    };

    carousel.addEventListener('pointerdown', (event) => {
      isDown = true;
      carousel.classList.add('dragging');
      startX = event.clientX;
      startScrollLeft = carousel.scrollLeft;
      pauseBriefly(2400);
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
      pauseBriefly(1600);
      carousel.releasePointerCapture?.(event.pointerId);
    };

    carousel.addEventListener('pointerup', stopDragging);
    carousel.addEventListener('pointercancel', stopDragging);
    carousel.addEventListener('mouseleave', () => {
      if (isDown) carousel.classList.remove('dragging');
      isDown = false;
    });
    carousel.addEventListener('mouseenter', () => pauseBriefly(800));
    carousel.addEventListener('wheel', () => {
      pauseBriefly(1800);
      requestAnimationFrame(normalizeScroll);
    }, { passive: true });

    const autoplay = (now) => {
      const elapsed = Math.min((now - lastFrame) / 1000, 0.08);
      lastFrame = now;
      if (!isDown && now > pausedUntil && halfWidth() > carousel.clientWidth) {
        carousel.scrollLeft += speed * elapsed;
        normalizeScroll();
      }
      requestAnimationFrame(autoplay);
    };

    requestAnimationFrame(() => {
      carousel.scrollLeft = 1;
      requestAnimationFrame(autoplay);
    });
  });
});
