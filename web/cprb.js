/**
 * @file Entry point for the comfyui-premiere-bridge frontend extension
 * (PROTOCOL.md §7). Tier 1 has no graph behavior — just the About badge and
 * the version-mismatch settings section, matching the cpsb pattern.
 */

import { app } from '../../scripts/app.js'
import { FRONTEND_VERSION, warn } from './cprb/api.js'
import { SETTINGS, initSettings } from './cprb/settings.js'

const REPO_URL = 'https://github.com/ericpaulsnowden/comfyui-premiere-bridge'

app.registerExtension({
  name: 'cprb.PremiereBridge',
  settings: SETTINGS,
  aboutPageBadges: [
    {
      label: `Premiere Bridge v${FRONTEND_VERSION}`,
      url: REPO_URL,
      icon: 'pi pi-github'
    }
  ],

  async setup() {
    try {
      await initSettings()
    } catch (error) {
      warn('initSettings failed', error)
    }
  }
})
