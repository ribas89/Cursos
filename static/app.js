(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];

  // Light is the default; only an explicit dark-mode preference overrides it.
  const themeButton = $('#theme-toggle');
  const themeIcon = $('.theme-icon');
  function refreshTheme() {
    if (themeIcon) themeIcon.className = 'theme-icon bi ' + (document.documentElement.dataset.theme === 'dark' ? 'bi-sun' : 'bi-moon-stars');
  }
  refreshTheme();
  themeButton?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('cursos-theme', next); } catch (_) {}
    refreshTheme();
  });

  const accountMenu = $('.account-menu');
  document.addEventListener('click', event => {
    if (accountMenu?.open && !accountMenu.contains(event.target)) accountMenu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && accountMenu?.open) accountMenu.open = false;
  });

  // Browser-only guest profile. Never store account credentials here.
  function readLocal(key, fallback) {
    try { const value = JSON.parse(localStorage.getItem(key)); return value ?? fallback; }
    catch (_) { return fallback; }
  }
  function writeLocal(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  }
  const guestKey = course => 'cursos-guest-progress-v1:' + encodeURIComponent(course);
  const favoritesKey = 'cursos-guest-favorites-v1';
  const startedKey = 'cursos-guest-started-v1';
  function guestProgress(course) {
    const value = readLocal(guestKey(course), {});
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }
  function saveGuest(course, path, fields) {
    const records = guestProgress(course);
    const previous = Object.hasOwn(records, path) && records[path] && typeof records[path] === 'object' ? records[path] : {};
    const next = {
      completed: typeof fields.completed === 'boolean' ? fields.completed : previous.completed === true,
      seconds: Number.isFinite(fields.seconds) ? Math.max(0, Math.min(864000, Math.floor(fields.seconds))) : (Number(previous.seconds) || 0)
    };
    // Handles filenames such as __proto__ without mutating the records prototype.
    Object.defineProperty(records, path, {value:next, enumerable:true, configurable:true, writable:true});
    writeLocal(guestKey(course), records);
    return next;
  }
  function guestSet(key) {
    const raw = readLocal(key, []);
    return new Set(Array.isArray(raw) ? raw.filter(x => typeof x === 'string') : []);
  }
  function markStarted(course) {
    if (!course) return;
    try { const started = guestSet(startedKey); started.add(course); writeLocal(startedKey, [...started]); }
    catch (err) { console.warn('Unable to save course history:', err); }
  }

  const library = $('#library-layout');
  if (library) {
    const signedIn = library.dataset.guest === 'false';
    let serverState = {favorites: [], started: []};
    try { serverState = JSON.parse($('#library-state')?.textContent || '{}'); } catch (_) {}
    let strings = {};
    try { strings = JSON.parse($('#library-ui')?.textContent || '{}'); } catch (_) {}
    const favorites = signedIn ? new Set(serverState.favorites || []) : guestSet(favoritesKey);
    const started = signedIn ? new Set(serverState.started || []) : guestSet(startedKey);
    const cards = $$('.course-card[data-course-id]');
    const search = $('#course-search');
    const tabs = $$('.library-tab');
    const count = $('#visible-count');
    const empty = $('#catalog-empty');
    const emptyTitle = $('#catalog-empty-title');
    const emptyHint = $('#catalog-empty-hint');
    let active = ['all','favorites','started'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'all';

    function updateCard(card) {
      const id = card.dataset.courseId;
      const button = card.querySelector('.favorite-button');
      const isFav = favorites.has(id);
      if (button) {
        button.setAttribute('aria-pressed', String(isFav));
        button.setAttribute('aria-label', isFav ? strings.favorite_remove : strings.favorite_add);
        button.title = isFav ? strings.favorite_remove : strings.favorite_add;
        const icon = button.querySelector('span');
        if (icon) icon.className = 'bi ' + (isFav ? 'bi-star-fill' : 'bi-star');
      }
      if (!signedIn) {
        const records = guestProgress(id);
        const done = Object.values(records).filter(item => item?.completed === true).length;
        const label = card.querySelector('.course-card-progress');
        if (label) label.textContent = done ? `${done} ✓` : '';
      }
    }
    function render() {
      let visible = 0;
      const query = (search?.value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase();
      cards.forEach(card => {
        updateCard(card);
        const id = card.dataset.courseId;
        const name = card.dataset.courseName.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase();
        const match = name.includes(query) && (active === 'all' || (active === 'favorites' ? favorites.has(id) : started.has(id)));
        card.hidden = !match;
        if (match) visible++;
      });
      tabs.forEach(tab => {
        const selected = tab.dataset.filter === active;
        tab.classList.toggle('active', selected);
        tab.setAttribute('aria-pressed', String(selected));
      });
      if ($('#favorites-count')) $('#favorites-count').textContent = favorites.size || '';
      if ($('#started-count')) $('#started-count').textContent = started.size || '';
      if (count) count.textContent = (strings.count_of || '{shown} of {total} courses').replace('{shown}', String(visible)).replace('{total}', String(cards.length));
      if (empty) empty.hidden = visible > 0;
      if (visible === 0 && emptyTitle) {
        emptyTitle.textContent = query ? strings.no_courses : active === 'favorites' ? strings.no_favorites : active === 'started' ? strings.no_progress : strings.no_courses;
        emptyHint.textContent = query ? strings.search_try_again : active === 'favorites' ? strings.star_hint : active === 'started' ? strings.start_hint : strings.search_try_again;
      }
    }
    search?.addEventListener('input', render);
    tabs.forEach(tab => tab.addEventListener('click', () => {
      active = tab.dataset.filter;
      history.replaceState(null, '', location.pathname + location.search + (active === 'all' ? '' : '#' + active));
      render();
    }));
    window.addEventListener('hashchange', () => {
      active = ['favorites','started'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'all';
      render();
    });
    cards.forEach(card => {
      const course = card.dataset.courseId;
      card.querySelector('.favorite-button')?.addEventListener('click', async () => {
        const desired = !favorites.has(course);
        const button = card.querySelector('.favorite-button');
        if (button) button.disabled = true;
        try {
          if (signedIn) {
            const endpoint = library.dataset.favoriteUrl.replace('__COURSE__', encodeURIComponent(course));
            const response = await fetch(endpoint, {
              method:'POST', credentials:'same-origin',
              headers:{'Content-Type':'application/json','X-CSRF-Token':library.dataset.csrf},
              body:JSON.stringify({favorite:desired})
            });
            if (!response.ok) throw new Error(strings.error_save || 'Could not save');
          } else {
            const updated = new Set(favorites);
            desired ? updated.add(course) : updated.delete(course);
            writeLocal(favoritesKey, [...updated]);
          }
          desired ? favorites.add(course) : favorites.delete(course);
          render();
        } catch (err) { alert(err.message); }
        finally { if (button) button.disabled = false; }
      });
    });
    render();

    // A fresh random meme is selected by /api/meme on every homepage visit.
    const memeImage = $('#meme-image');
    const memeOpen = $('#meme-open');
    const memeDialog = $('#meme-dialog');
    const memeDialogImage = $('#meme-dialog-image');
    const memeClose = $('#meme-close');
    const memeError = $('.meme-error');
    memeImage?.addEventListener('error', () => {
      memeImage.hidden = true;
      if (memeOpen) memeOpen.hidden = true;
      if (memeError) memeError.hidden = false;
    });
    memeImage?.addEventListener('load', () => {
      memeImage.hidden = false;
      if (memeOpen) memeOpen.hidden = false;
      if (memeError) memeError.hidden = true;
    });
    memeOpen?.addEventListener('click', () => {
      if (!memeImage?.complete || !memeImage.naturalWidth || !memeDialog || !memeDialogImage) return;
      memeDialogImage.src = memeImage.currentSrc || memeImage.src;
      if (typeof memeDialog.showModal === 'function') memeDialog.showModal();
    });
    memeClose?.addEventListener('click', () => memeDialog?.close());
    memeDialog?.addEventListener('click', event => {
      if (event.target === memeDialog) memeDialog.close(); // Outside the image.
    });
    // Browsers can restore the homepage from back/forward cache without reloading.
    window.addEventListener('pageshow', event => {
      if (!event.persisted || !memeImage) return;
      const url = new URL(memeImage.src, location.href);
      url.searchParams.set('visit', String(Date.now()));
      memeImage.src = url.href;
    });
  }

  const overview = $('.course-overview');
  if (overview?.dataset.guest === 'true') {
    const course = overview.dataset.courseId;
    markStarted(course);
    const records = guestProgress(course);
    const rows = $$('.file-row[data-lesson-path]');
    let done = 0;
    rows.forEach(row => {
      const complete = records[row.dataset.lessonPath]?.completed === true;
      row.classList.toggle('completed', complete);
      const marker = row.querySelector('.done-icon,.file-chevron');
      if (marker) { marker.className = complete ? 'done-icon bi bi-check-lg' : 'file-chevron bi bi-chevron-right'; marker.textContent = '';  }
      if (complete) done++;
    });
    $$('.folder').forEach(folder => {
      const counter = folder.querySelector(':scope > summary .folder-stats');
      if (counter) counter.textContent = `${folder.querySelectorAll('.file-row.completed').length}/${counter.dataset.folderTotal}`;
    });
    const percent = rows.length ? Math.round(done / rows.length * 100) : 0;
    if ($('#course-done')) $('#course-done').textContent = String(done);
    if ($('#course-percent')) $('#course-percent').textContent = String(percent);
    if ($('#course-progress-bar')) $('#course-progress-bar').style.width = percent + '%';
    $('#course-progress')?.setAttribute('aria-valuenow', String(percent));
  }

  const controls = $('#lesson-controls');
  if (!controls) return;
  const guest = controls.dataset.guest === 'true';
  const courseId = controls.dataset.courseId;
  if (guest) markStarted(courseId);
  const path = controls.dataset.path;
  const csrf = controls.dataset.csrf;
  const media = $('#lesson-media');
  // iOS WebKit expands/intercepts embedded PDF scrolling. Never load that
  // native iframe on iOS; use real page images in the document scroll instead.
  const nativePdfFrame = $('.lesson-media .native-pdf');
  const iosPages = $('#ios-pdf-pages');
  const iosFallback = $('#ios-pdf-fallback');
  const device = typeof navigator !== 'undefined' ? navigator : {};
  const isiOS = /iPhone|iPad|iPod/.test(device.userAgent || '') ||
                (device.platform === 'MacIntel' && (device.maxTouchPoints || 0) > 1);
  const iosPdfMode = Boolean(nativePdfFrame && isiOS);
  if (nativePdfFrame) {
    if (iosPdfMode) {
      nativePdfFrame.hidden = true;
      if (iosPages) {
        iosPages.hidden = false;
        // WebKit can flag many pages as visible at once. Queue them: only
        // ONE network request runs at a time, and failed requests are retried.
        const images = [...iosPages.querySelectorAll('img[data-src]')];
        const queue = [];
        const states = new WeakMap();
        const canvasImages = new WeakMap();
        let active = null;
        let observer = null;
        const MAX_ATTEMPTS = 3;
        const enqueue = (img, manual = false) => {
          const info = states.get(img);
          if (!info || info.state === 'queued' || info.state === 'loading' ||
              info.state === 'loaded' || info.state === 'waiting') return;
          if (info.state === 'failed' && !manual) return;
          if (manual) info.attempts = 0;
          info.state = 'queued';
          queue.push(img);
          pump();
        };
        const pump = () => {
          if (active || !queue.length) return;
          const img = queue.shift();
          const info = states.get(img);
          if (!info || info.state !== 'queued') { pump(); return; }
          active = img;
          info.state = 'loading';
          info.attempts++;
          info.retry.hidden = true;
          info.message.hidden = false;
          // The versioned URL is immutable; only failed attempts use a new
          // query key to bypass cached error responses in Safari/proxies.
          const separator = info.url.includes('?') ? '&' : '?';
          img.src = info.url + (info.attempts > 1 ? separator + 'attempt=' + info.attempts : '');
        };
        images.forEach(img => {
          const canvas = img.closest('.ios-pdf-canvas');
          const status = canvas?.querySelector('.ios-pdf-state');
          const message = status?.querySelector('span');
          const retry = status?.querySelector('.ios-pdf-retry');
          if (!canvas || !status || !message || !retry) return;
          const info = {url:img.dataset.src, state:'idle', attempts:0,
                        status, message, retry};
          states.set(img, info);
          canvasImages.set(canvas, img);
          retry.addEventListener('click', () => enqueue(img, true));
          img.addEventListener('load', () => {
            if (info.state !== 'loading') return;
            info.state = 'loaded';
            img.hidden = false;
            status.hidden = true;
            observer?.unobserve(img.closest('.ios-pdf-canvas'));
            active = null;
            pump();
          });
          img.addEventListener('error', () => {
            if (info.state !== 'loading') return;
            img.removeAttribute('src'); // no broken-image icon
            img.hidden = true;
            active = null;
            if (info.attempts < MAX_ATTEMPTS) {
              info.state = 'waiting';
              const delay = 800 * info.attempts;
              setTimeout(() => { info.state = 'idle'; enqueue(img); }, delay);
            } else {
              info.state = 'failed';
              message.hidden = true;
              retry.hidden = false; // user can retry any time
              observer?.unobserve(img.closest('.ios-pdf-canvas'));
            }
            pump();
          });
        });
        if (typeof IntersectionObserver !== 'undefined') {
          observer = new IntersectionObserver(entries => {
            entries.forEach(entry => {
              if (entry.isIntersecting) {
                const image = canvasImages.get(entry.target);
                if (image) enqueue(image);
              }
            });
          }, {rootMargin: '350px 0px'});
          images.filter(img => states.has(img)).forEach(img => observer.observe(img.closest('.ios-pdf-canvas')));
        } else {
          // Legacy Safari: sequentially load all pages, rather than sending
          // dozens of concurrent requests through Gunicorn/PDFium.
          images.filter(img => states.has(img)).forEach(img => enqueue(img));
        }
      } else if (iosFallback) {
        iosFallback.hidden = false;
      }
    } else {
      // Non-iOS: keep the browser's searchable native PDF interface.
      nativePdfFrame.src = nativePdfFrame.dataset.pdfSrc;
    }
  }
  // Fit embedded PDF/HTML/text viewers to the *remaining* browser viewport.
  // The toolbar is not sticky: it occupies normal flow above the iframe.
  // On resize (including docked developer tools), recalculate rather than
  // imposing a minimum iframe height larger than the available viewport.
  const documentFrame = $('.lesson-media .doc-frame');
  function fitDocumentToViewport() {
    if (!documentFrame) return;
    const layoutHeight = window.innerHeight || document.documentElement.clientHeight || 600;
    const visualHeight = window.visualViewport?.height || layoutHeight;
    const viewportHeight = Math.min(layoutHeight, visualHeight);
    // Use the element's *document* position, not its scrolled screen position:
    // resizing while midway down a document must not inflate iframe height.
    const top = documentFrame.getBoundingClientRect().top + (window.scrollY || 0);
    const freeHeight = Math.max(140, Math.floor(viewportHeight - top - 1));
    documentFrame.style.height = freeHeight + 'px';
  }
  if (documentFrame && !iosPdfMode) {
    fitDocumentToViewport();
    window.addEventListener?.('resize', fitDocumentToViewport);
    window.visualViewport?.addEventListener?.('resize', fitDocumentToViewport);
    const toolbar = $('.lesson-toolbar');
    if (toolbar && typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(fitDocumentToViewport).observe(toolbar);
    }
  }
  // Size videos by their intrinsic dimensions, not a forced 16:9 box.
  // Single toolbar is above the player; no bottom action bar exists.
  function fitVideoToViewport() {
    if (!media || controls.dataset.kind !== 'video' || typeof media.getBoundingClientRect !== 'function') return;
    const container = media.parentElement;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    const top = Math.max(0, media.getBoundingClientRect().top);
    const freeHeight = Math.max(120, viewportHeight - top - 12);
    const maxWidth = container?.clientWidth || (window.innerWidth || 1280) - 32;
    const aspect = media.videoWidth > 0 && media.videoHeight > 0 ? media.videoWidth / media.videoHeight : 16 / 9;
    if (!Number.isFinite(aspect) || aspect <= 0) return;
    const width = Math.floor(Math.min(maxWidth, freeHeight * aspect));
    const height = Math.floor(width / aspect);
    media.style.width = width + 'px';
    media.style.height = height + 'px';
  }
  if (controls.dataset.kind === 'video') {
    media?.addEventListener('loadedmetadata', fitVideoToViewport);
    window.addEventListener?.('resize', fitVideoToViewport);
    // Initial fit before metadata: use 16:9 until the actual dimensions are known.
    fitVideoToViewport();
  }
  const completedButtons = $$('.complete-button');
  const progressStatus = $('#lesson-progress-status');
  const savedGuest = guest ? guestProgress(courseId)[path] : null;
  const initial = savedGuest && typeof savedGuest === 'object' ? savedGuest : {};
  const initialCompleted = guest ? initial.completed === true : controls.dataset.completed === 'true';
  const resume = guest ? Number(initial.seconds || 0) : Number(controls.dataset.seconds || 0);
  let savePending = false;
  let lastSave = 0;
  let advancing = false;

  async function save(fields) {
    if (guest) return saveGuest(courseId, path, fields);
    const response = await fetch(controls.dataset.endpoint, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
      body: JSON.stringify({path, ...fields}), keepalive: true
    });
    if (!response.ok) throw new Error('Progress could not be saved');
    return response.json();
  }
  function showComplete(value) {
    completedButtons.forEach(button => {
      button.dataset.completed = String(value);
      button.textContent = value ? controls.dataset.undoLabel : controls.dataset.markLabel;
      button.dataset.short = value ? controls.dataset.undoShort : controls.dataset.markShort;
      button.setAttribute('aria-label', button.textContent);
    });
    if (progressStatus) progressStatus.textContent = value ? controls.dataset.completeLabel : controls.dataset.progressLabel;
  }
  showComplete(initialCompleted);
  completedButtons.forEach(button => button.addEventListener('click', async () => {
    completedButtons.forEach(item => { item.disabled = true; });
    try {
      const fields = {completed: button.dataset.completed !== 'true'};
      if (media && Number.isFinite(media.currentTime) && media.currentTime >= 0) fields.seconds = Math.round(media.currentTime);
      const result = await save(fields);
      showComplete(result.completed);
    } catch (err) { alert(err.message); }
    finally { completedButtons.forEach(item => { item.disabled = false; }); }
  }));
  if (!media) return;
  media.addEventListener('loadedmetadata', () => {
    if (resume > 2 && Number.isFinite(media.duration) && resume < media.duration - 3) {
      try { media.currentTime = resume; } catch (_) {}
    }
  }, {once:true});
  function savePosition() {
    if (savePending || advancing || !Number.isFinite(media.currentTime) || media.currentTime <= 0) return;
    savePending = true;
    save({seconds:Math.floor(media.currentTime)}).catch(console.error).finally(() => { savePending = false; });
  }
  media.addEventListener('timeupdate', () => {
    if (Date.now() - lastSave > 15000) { lastSave = Date.now(); savePosition(); }
  });
  media.addEventListener('pause', savePosition);

  media.addEventListener('ended', async () => {
    if (advancing) return;
    advancing = true;
    try {
      const result = await save({seconds: Math.floor(media.duration || media.currentTime || 0), completed:true});
      showComplete(result.completed);
    } catch (err) { console.error(err); alert(err.message); }
    finally { advancing = false; }
  });
})();

// Images use the browser's native decoder. If this browser cannot display a
// format (such as HEIC or TIFF on some platforms), keep the download option.
document.querySelectorAll('.lesson-inline-image').forEach(img => {
  const showFallback = () => {
    img.hidden = true;
    const fallback = img.parentElement?.querySelector('.lesson-image-error');
    if (fallback) fallback.hidden = false;
  };
  img.addEventListener('error', showFallback);
  if (img.complete && img.naturalWidth === 0) showFallback();
});
