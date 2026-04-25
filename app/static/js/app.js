console.log('Railway Manager MVP loaded');

(function () {
  const initProjectsCarousel = () => {
    document.querySelectorAll('[data-projects-carousel]').forEach((carousel) => {
      if (carousel.dataset.carouselReady === '1') return;

      const track = carousel.querySelector('.projects-carousel-track');
      if (!track || !track.children.length) return;
      carousel.dataset.carouselReady = '1';

      const speed = Number(carousel.dataset.speed || 52); // px por segundo
      let loopWidth = 0;
      let offset = 0;
      let lastFrame = performance.now();
      let isDragging = false;
      let isTouching = false;
      let startX = 0;
      let startOffset = 0;
      let moved = false;
      let holdTimer = null;
      let longPressPaused = false;
      let pausedUntil = 0;

      const getCopies = () => Math.max(Number(track.dataset.loopCopies || 8), 2);

      const measure = () => {
        const copies = getCopies();
        loopWidth = track.scrollWidth / copies;
        if (!Number.isFinite(loopWidth) || loopWidth <= 0) loopWidth = 0;
        normalizeOffset();
        applyTransform();
      };

      const normalizeOffset = () => {
        if (!loopWidth) return;
        offset %= loopWidth;
        if (offset < 0) offset += loopWidth;
      };

      const applyTransform = () => {
        track.style.transform = `translate3d(${-offset}px, 0, 0)`;
      };

      const pauseBriefly = (duration = 500) => {
        pausedUntil = performance.now() + duration;
      };

      const clearHoldTimer = () => {
        if (holdTimer) {
          clearTimeout(holdTimer);
          holdTimer = null;
        }
      };

      const stopDragging = (event) => {
        clearHoldTimer();
        if (!isDragging) return;

        isDragging = false;
        isTouching = false;
        longPressPaused = false;
        carousel.classList.remove('dragging', 'is-holding');
        pauseBriefly(180);

        try { carousel.releasePointerCapture(event.pointerId); } catch (_) {}
      };

      carousel.addEventListener('pointerdown', (event) => {
        if (event.button !== undefined && event.button !== 0) return;

        isDragging = true;
        isTouching = event.pointerType === 'touch';
        moved = false;
        longPressPaused = false;
        startX = event.clientX;
        startOffset = offset;
        carousel.classList.add('dragging');

        clearHoldTimer();
        if (isTouching) {
          holdTimer = setTimeout(() => {
            if (isDragging && !moved) {
              longPressPaused = true;
              carousel.classList.add('is-holding');
            }
          }, 360);
        } else {
          pauseBriefly(900);
        }

        try { carousel.setPointerCapture(event.pointerId); } catch (_) {}
      });

      carousel.addEventListener('pointermove', (event) => {
        if (!isDragging) return;

        const dx = event.clientX - startX;
        if (Math.abs(dx) > 6) {
          moved = true;
          longPressPaused = false;
          carousel.classList.remove('is-holding');
          clearHoldTimer();
        }

        // Arrastar para a esquerda avança o carrossel; para a direita volta.
        offset = startOffset - dx;
        normalizeOffset();
        applyTransform();

        if (!isTouching || Math.abs(dx) > 10) {
          event.preventDefault();
        }
      }, { passive: false });

      carousel.addEventListener('pointerup', stopDragging);
      carousel.addEventListener('pointercancel', stopDragging);
      carousel.addEventListener('lostpointercapture', stopDragging);

      carousel.addEventListener('wheel', (event) => {
        if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
          offset += event.deltaX;
          normalizeOffset();
          applyTransform();
          pauseBriefly(700);
        }
      }, { passive: true });

      window.addEventListener('resize', measure);

      const autoplay = (now) => {
        const elapsed = Math.min((now - lastFrame) / 1000, 0.08);
        lastFrame = now;

        const shouldRun = !document.hidden && (!isDragging || longPressPaused) && now > pausedUntil;
        const shouldStayPaused = isDragging && (moved || longPressPaused);

        if (loopWidth && shouldRun && !shouldStayPaused) {
          offset += speed * elapsed;
          normalizeOffset();
          applyTransform();
        }

        requestAnimationFrame(autoplay);
      };

      const start = () => {
        measure();
        lastFrame = performance.now();
        requestAnimationFrame(autoplay);
      };

      if (document.readyState === 'complete') start();
      else window.addEventListener('load', start, { once: true });

      // Garante medida correta mesmo se imagens demorarem para calcular largura.
      setTimeout(measure, 400);
      setTimeout(measure, 1200);
    });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initProjectsCarousel);
  else initProjectsCarousel();
})();
