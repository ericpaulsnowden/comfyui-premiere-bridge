/**
 * @file Entry point for the comfyui-premiere-bridge frontend extension
 * (PROTOCOL.md §7). Tier 1's only graph behavior is the §7.3 file bar
 * (Browse…/Open folder/Open output folder) on PremiereLoadTimeline/
 * PremiereSaveTimeline nodes, attached from `nodeCreated`; everything else
 * is the About badge and the version-mismatch settings section, matching
 * the cpsb pattern.
 */

import { app } from '../../scripts/app.js'
import { FRONTEND_VERSION, warn } from './cprb/api.js'
import { SETTINGS, initSettings } from './cprb/settings.js'
import { attachNodeUi } from './cprb/nodes.js'
import { initSendResultToasts } from './cprb/send_result.js'

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
    // Independent of initSettings: a backend that's unreachable at load
    // (so the version fetch above failed) must still get its Send-to-
    // Premiere toasts once it comes back — PROTOCOL.md §10.6.
    try {
      initSendResultToasts()
    } catch (error) {
      warn('initSendResultToasts failed', error)
    }
  },

  /**
   * Fires once per node instance. `attachNodeUi` is itself a no-op (and
   * never throws) for any node that isn't PremiereLoadTimeline/
   * PremiereSaveTimeline (PROTOCOL.md §7.3) — wrapped here too, belt and
   * suspenders, matching this file's `setup()` above.
   */
  nodeCreated(node) {
    try {
      attachNodeUi(node)
    } catch (error) {
      warn('attachNodeUi failed', error)
    }
  }
})
