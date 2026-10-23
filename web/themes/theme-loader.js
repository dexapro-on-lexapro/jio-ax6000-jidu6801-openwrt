(function() {
  // 1. Immediately inject the active theme link if not present
  if (!document.getElementById('jio-active-theme')) {
    var link = document.createElement('link');
    link.id = 'jio-active-theme';
    link.rel = 'stylesheet';
    link.href = '/themes/active-theme.css?t=' + Date.now();
    document.head.appendChild(link);
  }

  // 2. Add Top Navigation Bar to Stock Dashboard
  function injectTopNav() {
    if (document.getElementById('jio-portal-nav')) return;

    var nav = document.createElement('div');
    nav.id = 'jio-portal-nav';
    nav.style.cssText = [
      'position: fixed',
      'top: 0',
      'left: 0',
      'right: 0',
      'height: 42px',
      'background: var(--jioWrtWhite)',
      'border-bottom: 1px solid var(--jioWrtBorder)',
      'display: flex',
      'align-items: center',
      'justify-content: space-between',
      'padding: 0 24px',
      'box-shadow: 0 2px 8px var(--jioWrtShadow)',
      'z-index: 99999',
      'font-family: JioTypeMedium, -apple-system, sans-serif',
      'font-size: 13px',
      'box-sizing: border-box'
    ].join(';');

    nav.innerHTML = [
      '<div style="font-family: JioTypeBold, sans-serif; color: var(--jioWrtPrimary); font-size: 14px; display: flex; align-items: center; gap: 8px;">',
      '  <span>🌐</span> Jio AX6000 Multi-Portal',
      '</div>',
      '<div style="display: flex; gap: 8px;">',
      '  <a href="/" style="padding: 5px 12px; border-radius: 14px; background: var(--jioWrtPrimary); color: #fff; text-decoration: none; font-weight: bold;">🏠 Stock Dashboard</a>',
      '  <a href="/wan-settings.html" style="padding: 5px 12px; border-radius: 14px; background: var(--jioWrtPageBG); color: var(--jioWrtText); text-decoration: none; border: 1px solid var(--jioWrtBorder);">🌐 WAN Setup</a>',
      '  <a href="/theme-settings.html" style="padding: 5px 12px; border-radius: 14px; background: var(--jioWrtPageBG); color: var(--jioWrtText); text-decoration: none; border: 1px solid var(--jioWrtBorder);">🎨 Themes</a>',
      '  <a href="http://' + window.location.hostname + ':8888/" target="_blank" style="padding: 5px 12px; border-radius: 14px; background: var(--jioWrtPageBG); color: var(--jioWrtText); text-decoration: none; border: 1px solid var(--jioWrtBorder);">⚡ OpenWrt LuCI</a>',
      '  <a href="http://' + window.location.hostname + ':3000/" target="_blank" style="padding: 5px 12px; border-radius: 14px; background: var(--jioWrtPageBG); color: var(--jioWrtText); text-decoration: none; border: 1px solid var(--jioWrtBorder);">🛡️ AdGuard Home</a>',
      '</div>'
    ].join('');

    document.body.prepend(nav);

    // Push the React app content down so the top bar does not overlap
    var root = document.getElementById('root');
    if (root) {
      root.style.marginTop = '42px';
      root.style.minHeight = 'calc(100vh - 42px)';
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectTopNav);
  } else {
    injectTopNav();
  }
})();
