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

(function () {
  const closeFlash = (flash) => {
    if (!flash || flash.dataset.closing === '1') return;
    flash.dataset.closing = '1';
    flash.classList.add('is-hiding');
    window.setTimeout(() => flash.remove(), 240);
  };

  const initHeaderAndFlash = () => {
    document.querySelectorAll('[data-user-menu]').forEach((menu) => {
      if (menu.dataset.menuReady === '1') return;
      menu.dataset.menuReady = '1';
      const toggle = menu.querySelector('[data-user-menu-toggle]');
      if (!toggle) return;

      const setOpen = (open) => {
        menu.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      };

      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        setOpen(!menu.classList.contains('is-open'));
      });

      document.addEventListener('click', (event) => {
        if (!menu.contains(event.target)) setOpen(false);
      });

      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') setOpen(false);
      });
    });

    document.querySelectorAll('[data-flash-message]').forEach((flash) => {
      if (flash.dataset.flashReady === '1') return;
      flash.dataset.flashReady = '1';
      const closeButton = flash.querySelector('[data-flash-close]');
      if (closeButton) closeButton.addEventListener('click', () => closeFlash(flash));
      window.setTimeout(() => closeFlash(flash), 5000);
    });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initHeaderAndFlash);
  else initHeaderAndFlash();
})();

(function () {
  const initFakeDashboards = () => {
    document.querySelectorAll('[data-fake-dashboard]').forEach((dashboard) => {
      if (dashboard.dataset.fakeDashboardReady === '1') return;
      dashboard.dataset.fakeDashboardReady = '1';

      const surface = dashboard.querySelector('[data-dashboard-surface]');
      const themeToggle = dashboard.querySelector('[data-dashboard-theme-toggle]');
      const submenuTrigger = dashboard.querySelector('[data-fake-submenu-trigger]');
      const submenu = dashboard.querySelector('[data-fake-submenu]');
      const navItems = dashboard.querySelectorAll('.fake-nav-item');
      const metricCards = dashboard.querySelectorAll('.fake-metric-card');
      const chart = dashboard.querySelector('.fake-hover-chart');
      const chartBars = dashboard.querySelectorAll('.fake-chart-bar');
      const tooltip = dashboard.querySelector('[data-chart-tooltip]');

      const setThemeLabel = () => {
        if (!surface || !themeToggle) return;
        const isLight = surface.classList.contains('is-light');
        const icon = themeToggle.querySelector('i');
        const label = themeToggle.querySelector('span');
        if (icon) icon.className = isLight ? 'bi bi-moon-stars-fill' : 'bi bi-sun-fill';
        if (label) label.textContent = isLight ? 'Dark' : 'Light';
      };

      if (themeToggle && surface) {
        setThemeLabel();
        themeToggle.addEventListener('click', () => {
          surface.classList.toggle('is-light');
          setThemeLabel();
        });
      }

      navItems.forEach((item) => {
        item.addEventListener('click', () => {
          if (item === submenuTrigger) return;
          navItems.forEach((other) => other.classList.remove('is-active'));
          item.classList.add('is-active');
        });
      });

      if (submenuTrigger && submenu) {
        submenuTrigger.addEventListener('click', () => {
          const isOpen = submenu.classList.toggle('is-open');
          submenuTrigger.classList.toggle('is-open', isOpen);
          submenuTrigger.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });
      }

      metricCards.forEach((card) => {
        card.addEventListener('mouseenter', () => {
          metricCards.forEach((other) => other.classList.remove('is-active'));
          card.classList.add('is-active');
        });
        card.addEventListener('focus', () => {
          metricCards.forEach((other) => other.classList.remove('is-active'));
          card.classList.add('is-active');
        });
      });

      const showBarTooltip = (bar) => {
        if (!chart || !tooltip || !bar) return;
        chartBars.forEach((other) => other.classList.remove('is-active'));
        bar.classList.add('is-active');
        const value = Number(bar.dataset.value || 0);
        const label = bar.dataset.label || '';
        tooltip.textContent = `${label}: R$ ${(value * 0.19).toFixed(1)}k`;
        const chartRect = chart.getBoundingClientRect();
        const barRect = bar.getBoundingClientRect();
        tooltip.style.left = `${barRect.left - chartRect.left + barRect.width / 2}px`;
        tooltip.style.top = `${Math.max(14, barRect.top - chartRect.top - 36)}px`;
        tooltip.classList.add('is-visible');
      };

      const hideBarTooltip = () => {
        if (tooltip) tooltip.classList.remove('is-visible');
        chartBars.forEach((bar) => bar.classList.remove('is-active'));
      };

      chartBars.forEach((bar, index) => {
        bar.addEventListener('mouseenter', () => showBarTooltip(bar));
        bar.addEventListener('focus', () => showBarTooltip(bar));
        bar.addEventListener('mouseleave', hideBarTooltip);
        bar.addEventListener('blur', hideBarTooltip);
        if (index === 3) window.setTimeout(() => showBarTooltip(bar), 700);
        if (index === 3) window.setTimeout(hideBarTooltip, 2500);
      });
    });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initFakeDashboards);
  else initFakeDashboards();
})();
