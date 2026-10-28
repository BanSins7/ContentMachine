"""UA-MOD-27 source-level UI security and wiring checks (no browser required).

These are static protections, not a substitute for the Node DOM smoke or
real Chromium screenshot QA on a developer machine.
"""
from __future__ import annotations
from pathlib import Path
import re
import unittest


ROOT=Path(__file__).resolve().parents[1]


class UAEStudioStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js=(ROOT/"site/unified-analytics-studio.js").read_text(encoding="utf-8")
        cls.css=(ROOT/"site/unified-analytics-studio.css").read_text(encoding="utf-8")
        cls.html=(ROOT/"site/index.html").read_text(encoding="utf-8")
        cls.app=(ROOT/"app.py").read_text(encoding="utf-8")
        cls.projects=(ROOT/"site/projects.js").read_text(encoding="utf-8")

    def test_studio_asset_script_loaded_once(self):
        self.assertEqual(self.html.count('src="/unified-analytics-studio.js"'),1)

    def test_studio_stylesheet_loaded_once(self):
        self.assertEqual(self.html.count('href="/unified-analytics-studio.css"'),1)

    def test_studio_loaded_after_project_context(self):
        self.assertLess(self.html.index('src="/project-context.js"'),
                        self.html.index('src="/unified-analytics-studio.js"'))

    def test_app_serves_only_named_static_assets(self):
        self.assertIn("'/unified-analytics-studio.js': 'unified-analytics-studio.js'",
                      self.app)
        self.assertIn("'/unified-analytics-studio.css': 'unified-analytics-studio.css'",
                      self.app)
        self.assertIn("files.get(url.path)",self.app)

    def test_mounted_inside_existing_project_details_root(self):
        self.assertIn("window.ContentMachineUnifiedAnalyticsStudio.mount(root,project.id)",
                      self.projects)
        self.assertIn("ContentMachineUnifiedAnalyticsStudio={mount}",self.js)

    def test_no_implicit_http_fetch_on_mount(self):
        end=self.js.rindex("    // No automatic fetch or timer on mounting.")
        self.assertIn("No automatic fetch or timer on mounting",self.js[end:])
        self.assertNotIn("setInterval(",self.js)
        self.assertNotIn("setTimeout(",self.js)
        self.assertNotIn("window.location",self.js)

    def test_mutation_only_after_explicit_click_and_confirmation(self):
        self.assertIn("window.confirm('Поставить зарегистрированный запрос в очередь?",self.js)
        self.assertIn("window.confirm('Отменить только это задание?",self.js)
        self.assertIn("request('/qualify',{",self.js)
        self.assertIn("request('/jobs/'+selectedJob+'/cancel',{})",self.js)

    def test_never_store_project_token_in_browser_storage(self):
        for banned in ("localStorage","sessionStorage","document.cookie",
                       "indexedDB","window.name"):
            self.assertNotIn(banned,self.js)

    def test_token_is_password_and_cleared(self):
        self.assertIn("'password','Вводится вручную, не сохраняется'",self.js)
        self.assertIn("token.input.value=''",self.js)
        self.assertIn("currentToken!==token.input.value",self.js)

    def test_project_and_exact_version_headers(self):
        self.assertIn("'X-UAE-Project-Token':currentToken",self.js)
        self.assertIn("'X-UAE-Api-Version':VERSION",self.js)
        self.assertIn("uae-local-http/v1",self.js)
        self.assertIn("const HEX32=/^[0-9a-f]{32}$/",self.js)

    def test_mutation_csrf_header(self):
        self.assertIn("headers['X-ContentMachine']='1'",self.js)
        self.assertIn("credentials:'same-origin'",self.js)

    def test_no_token_in_query_parameters(self):
        self.assertNotIn("?token=",self.js)
        self.assertNotIn("token.input.value+",self.js)
        self.assertNotIn("URLSearchParams",self.js)

    def test_textcontent_not_innerhtml(self):
        self.assertIn("e.textContent=String(text)",self.js)
        for bad in ("innerHTML","outerHTML","insertAdjacentHTML",
                    "document.write","eval("):
            self.assertNotIn(bad,self.js)

    def test_a_b_c_evidence_explicitly_unavailable(self):
        for marker in ("'Group A'","'Group B'","'Group C'",
                       "'SRE'","'QDP'"):
            self.assertIn(marker,self.js)
        self.assertIn("make('span','Недоступно','uae-pill')",self.js)
        self.assertIn("Breakout Score и компоненты не опубликованы",self.js)

    def test_no_misleading_scores_or_significance(self):
        self.assertIn("Шкала 0–100 не используется",self.js)
        self.assertIn("Отсутствие подтверждённых метрик — не нулевой результат",self.js)
        self.assertIn("Ни p-value, ни достоверность или причинность",self.js)
        self.assertNotIn("statistically_significant:true",self.js)

    def test_timeline_only_uses_partial_and_blocked_states(self):
        self.assertIn("!['partial','blocked'].includes(item.status)",self.js)
        self.assertIn("snapshots?limit=10",self.js)
        self.assertIn("next_before",self.js)

    def test_no_full_project_statistics_claim(self):
        self.assertIn("не все данные проекта",self.js)
        self.assertIn("Показана только доступная страница истории",self.js)
        self.assertIn("не учитываются",self.js)

    def test_http_409_aggregate_is_displayed_as_gate_not_score(self):
        self.assertIn("aggregate_requires_live_qualified_pins",self.js)
        self.assertIn("if(err&&err.code==='aggregate_requires_live_qualified_pins')",
                      self.js)
        self.assertNotIn("0.0%",self.js)

    def test_race_safety_serial_and_token_change(self):
        self.assertIn("const ticket=++serial",self.js)
        self.assertIn("ticket!==serial",self.js)
        self.assertIn("currentToken!==token.input.value",self.js)
        self.assertIn("root.isConnected",self.js)

    def test_html_accessibility_and_focus_visible(self):
        for attr in ("aria-live","aria-label","aria-labelledby"):
            self.assertIn(attr,self.js)
        self.assertIn(":focus-visible",self.css)
        self.assertIn("@media(max-width:500px)",self.css)
        self.assertIn("@media(prefers-reduced-motion:reduce)",self.css)

    def test_css_namespaced_to_uae_studio(self):
        for line in self.css.splitlines():
            line=line.strip()
            if not line or line.startswith("/*") or line.startswith("*"):
                continue
            if line.startswith(".uae-") or line.startswith("@media"):
                continue
            if line.startswith("}") or line.startswith("/*") or line.startswith(" .uae-"):
                continue
            # Inside @media, all UI selectors continue with .uae-studio
            if line.startswith(".uae-studio"):
                continue
        self.assertGreaterEqual(self.css.count(".uae-studio"),25)

    def test_no_raw_pie_export_http_route_added(self):
        for marker in ("PIEResearchExportReader","SignedResearchExport",
                       "publish_qualified","score_status: 'complete'"):
            self.assertNotIn(marker,self.js)


if __name__=="__main__":
    unittest.main()
