/**
 * Collapsible sections for portfolio / spot intelligence dashboards.
 * Persists open/closed state per section in localStorage.
 */
(function initPortfolioPanelCollapse(global) {
  const DEFAULT_STORAGE_KEY = 'piDashboardPanelCollapse';

  function loadState(storageKey) {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) : {};
    } catch (_) {
      return {};
    }
  }

  function saveState(storageKey, state) {
    try {
      localStorage.setItem(storageKey, JSON.stringify(state));
    } catch (_) {
      // Ignore quota / private-mode errors.
    }
  }

  function slugify(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 48) || 'section';
  }

  function sectionKey(section, index) {
    if (section.id) return section.id;
    const title = section.querySelector('h2')?.textContent
      || section.querySelector('.pi-eyebrow')?.textContent
      || '';
    return `pi-section-${slugify(title)}-${index}`;
  }

  function wrapSectionBody(section, head, extraKeepers = []) {
    let body = section.querySelector(':scope > .pi-section-body');
    if (body) return body;

    const keep = new Set([head, ...extraKeepers].filter(Boolean));
    body = document.createElement('div');
    body.className = 'pi-section-body';
    [...section.children]
      .filter((child) => !keep.has(child))
      .forEach((child) => body.appendChild(child));
    section.appendChild(body);
    return body;
  }

  function ensureToggle(head, section, panelId, state, storageKey) {
    let toggle = head.querySelector('.pi-section-toggle');
    if (!toggle) {
      toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'pi-section-toggle';
      toggle.setAttribute('aria-label', 'Toggle section');
      toggle.innerHTML = '<i class="fas fa-chevron-up" aria-hidden="true"></i>';
      head.appendChild(toggle);
    }

    const body = section.querySelector(':scope > .pi-section-body');
    if (!body) return;

    const setCollapsed = (collapsed) => {
      section.classList.toggle('is-collapsed', collapsed);
      toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      toggle.title = collapsed ? 'Expand section' : 'Collapse section';
      state[panelId] = !collapsed;
      saveState(storageKey, state);
    };

    toggle.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      setCollapsed(!section.classList.contains('is-collapsed'));
    });

    if (state[panelId] === false) {
      setCollapsed(true);
    } else {
      setCollapsed(false);
    }
  }

  function setupHeroSection(section, panelId, state, storageKey) {
    const heroMain = section.querySelector(':scope > .pi-hero-main');
    const heroHead = heroMain?.querySelector('.pi-hero-head');
    if (!heroMain || !heroHead) return;

    heroHead.classList.add('pi-panel-head', 'pi-hero-collapse-head');

    let body = section.querySelector(':scope > .pi-section-body');
    if (!body) {
      body = document.createElement('div');
      body.className = 'pi-section-body';

      const mainBody = document.createElement('div');
      mainBody.className = 'pi-hero-main-body';
      let sibling = heroHead.nextElementSibling;
      while (sibling) {
        const next = sibling.nextElementSibling;
        mainBody.appendChild(sibling);
        sibling = next;
      }
      if (mainBody.childElementCount) body.appendChild(mainBody);

      const kpiGrid = section.querySelector(':scope > .pi-kpi-grid');
      if (kpiGrid) body.appendChild(kpiGrid);

      section.appendChild(body);
    }

    ensureToggle(heroHead, section, panelId, state, storageKey);
  }

  function setupSystemStrip(section, panelId, state, storageKey) {
    let head = section.querySelector(':scope > .pi-panel-head');
    if (!head) {
      head = document.createElement('div');
      head.className = 'pi-panel-head pi-system-strip-head';
      head.innerHTML = `
        <div>
          <p class="pi-eyebrow">System</p>
          <h2>Service health</h2>
        </div>`;
      section.insertBefore(head, section.firstChild);
    }
    wrapSectionBody(section, head);
    ensureToggle(head, section, panelId, state, storageKey);
  }

  function setupPanelSection(section, panelId, state, storageKey) {
    const head = section.querySelector(':scope > .pi-panel-head');
    if (!head) return;
    wrapSectionBody(section, head);
    ensureToggle(head, section, panelId, state, storageKey);
  }

  function initPanelCollapse(options = {}) {
    const storageKey = options.storageKey || DEFAULT_STORAGE_KEY;
    const root = options.root || document;
    const state = loadState(storageKey);
    const sections = root.querySelectorAll('.pi-shell > section');

    sections.forEach((section, index) => {
      section.classList.add('pi-section-collapsible');
      const panelId = sectionKey(section, index);
      section.dataset.collapseId = panelId;

      if (section.classList.contains('pi-hero')) {
        setupHeroSection(section, panelId, state, storageKey);
        return;
      }
      if (section.classList.contains('pi-system-strip')) {
        setupSystemStrip(section, panelId, state, storageKey);
        return;
      }
      setupPanelSection(section, panelId, state, storageKey);
    });
  }

  global.initPanelCollapse = initPanelCollapse;
})(window);
