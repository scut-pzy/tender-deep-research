/**
 * theme.js — Light/dark theme toggle with localStorage persistence
 */

const STORAGE_KEY = 'tender-theme';

export function initTheme() {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === 'light') {
    document.documentElement.classList.remove('dark');
  } else {
    document.documentElement.classList.add('dark');
  }
}

export function setupThemeToggle() {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;

  updateIcon(btn);

  btn.addEventListener('click', () => {
    const html = document.documentElement;
    html.classList.toggle('dark');
    const isDark = html.classList.contains('dark');
    localStorage.setItem(STORAGE_KEY, isDark ? 'dark' : 'light');
    updateIcon(btn);
  });
}

function updateIcon(btn) {
  const isDark = document.documentElement.classList.contains('dark');
  // Sun icon for dark mode (click to go light), moon for light mode (click to go dark)
  btn.innerHTML = isDark
    ? '<i data-lucide="sun" class="w-5 h-5"></i>'
    : '<i data-lucide="moon" class="w-5 h-5"></i>';
  btn.title = isDark ? '切换浅色模式' : '切换深色模式';
  // Re-render lucide icons for the new element
  if (window.lucide) lucide.createIcons({ nodes: [btn] });
}
