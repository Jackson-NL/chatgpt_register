import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camoufox.async_api import AsyncCamoufox

from app.services.registrator import select_country, set_react_input_value


class BrowserPhoneControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_country_selection_updates_the_e164_hidden_field(self):
        html = """
            <select tabindex="-1"><option value="SG">Singapore</option><option value="CO">Colombia</option></select>
            <input id="tel" type="tel">
            <input name="phone" type="hidden">
            <script>
                const country = document.querySelector('select');
                const tel = document.querySelector('#tel');
                const hidden = document.querySelector('[name=phone]');
                function sync() { hidden.value = country.value === 'CO' ? '+57' + tel.value : '+65' + tel.value; }
                country.addEventListener('change', sync);
                tel.addEventListener('input', sync);
            </script>
        """
        async with AsyncCamoufox(headless=True) as browser:
            page = await browser.new_page()
            await page.set_content(html)

            self.assertTrue(await select_country(page, "CO"))
            self.assertTrue(await set_react_input_value(page.locator("#tel"), "3181624184"))
            self.assertEqual(await page.locator('[name="phone"]').input_value(), "+573181624184")
