import setupApp from './app'

import '@klinecharts/pro/dist/klinecharts-pro.css'
import './index.css'

const root = document.querySelector<HTMLDivElement>('#app')!
if (window.location.pathname.startsWith('/screener')) {
  import('./screener/main').then((module) => module.mountScreener(root))
} else if (window.location.pathname.startsWith('/chat')) {
  import('./chat/main').then((module) => module.mountChat(root))
} else {
  setupApp(root)
}
