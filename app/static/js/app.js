console.log('Railway Manager MVP loaded');

(function () {
  const initProjectsCarousel = () => {
    document.querySelectorAll('[data-projects-carousel]').forEach((carousel) => {
      if (carousel.dataset.carouselReady === '1') return;
      const track = carousel.querySelector('.projects-carousel-track');
      if (!track || !track.children.length) return;
      carousel.dataset.carouselReady = '1';

      let isDragging = false;
      let startX = 0;
      let startScrollLeft = 0;
      let pausedUntil = 0;
      let lastFrame = performance.now();
      const speed = Number(carousel.dataset.speed || 55);
      const copies = Math.max(Number(track.dataset.loopCopies || 8), 2);

      const getLoopWidth = () => {
        const width = track.scrollWidth / copies;
        return Number.isFinite(width) && width > 0 ? width : 0;
      };

      const normalizeScroll = () => {
        const loopWidth = getLoopWidth();
        if (!loopWidth) return;
        const min = loopWidth * 2;
        const max = loopWidth * 4;
        if (carousel.scrollLeft >= max) carousel.scrollLeft -= loopWidth;
        else if (carousel.scrollLeft <= min - loopWidth) carousel.scrollLeft += loopWidth;
      };

      const centerLoop = () => {
        const loopWidth = getLoopWidth();
        if (!loopWidth) return;
        carousel.scrollLeft = loopWidth * 3;
        normalizeScroll();
      };

      const pauseBriefly = (duration = 900) => {
        pausedUntil = performance.now() + duration;
      };

      const stopDragging = (event) => {
        if (!isDragging) return;
        isDragging = false;
        carousel.classList.remove('dragging');
        pauseBriefly(700);
        try { carousel.releasePointerCapture(event.pointerId); } catch (_) {}
      };

      carousel.addEventListener('pointerdown', (event) => {
        isDragging = true;
        carousel.classList.add('dragging');
        startX = event.clientX;
        startScrollLeft = carousel.scrollLeft;
        pauseBriefly(1600);
        try { carousel.setPointerCapture(event.pointerId); } catch (_) {}
      });

      carousel.addEventListener('pointermove', (event) => {
        if (!isDragging) return;
        carousel.scrollLeft = startScrollLeft - (event.clientX - startX);
        normalizeScroll();
      });
      carousel.addEventListener('pointerup', stopDragging);
      carousel.addEventListener('pointercancel', stopDragging);
      carousel.addEventListener('pointerleave', stopDragging);
      carousel.addEventListener('scroll', normalizeScroll, { passive: true });
      carousel.addEventListener('wheel', () => pauseBriefly(1200), { passive: true });

      const autoplay = (now) => {
        const elapsed = Math.min((now - lastFrame) / 1000, 0.08);
        lastFrame = now;
        if (!isDragging && now > pausedUntil) {
          carousel.scrollLeft += speed * elapsed;
          normalizeScroll();
        }
        requestAnimationFrame(autoplay);
      };

      const start = () => {
        centerLoop();
        lastFrame = performance.now();
        requestAnimationFrame(autoplay);
      };

      if (document.readyState === 'complete') start();
      else {
        window.addEventListener('load', start, { once: true });
        setTimeout(start, 600);
      }
    });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initProjectsCarousel);
  else initProjectsCarousel();
})();
