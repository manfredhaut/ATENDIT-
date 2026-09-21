import ast
import os
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
APP_DIR = BACKEND_DIR / "app"
FRONTEND_DIR = APP_DIR / "frontend"

class TestFrontendSuite(unittest.TestCase):
    def test_01_frontend_html_files_exist(self):
        required_templates = ["login.html", "index.html", "admin.html"]
        for tpl in required_templates:
            tpl_path = FRONTEND_DIR / tpl
            self.assertTrue(tpl_path.is_file(), f"Template ausente: {tpl_path}")
            self.assertGreater(tpl_path.stat().st_size, 100, f"Template vazio: {tpl}")

    def test_02_ast_compilation_main_py(self):
        main_py_path = APP_DIR / "main.py"
        with open(main_py_path, "r", encoding="utf-8") as f:
            code = f.read()
        try:
            tree = ast.parse(code, filename=str(main_py_path))
            self.assertIsNotNone(tree)
        except SyntaxError as e:
            self.fail(f"Erro de sintaxe em main.py: {e}")

    def test_03_visual_routes_defined_in_main(self):
        main_py_path = APP_DIR / "main.py"
        with open(main_py_path, "r", encoding="utf-8") as f:
            content = f.read()
        expected_routes = [
            "@app.get(\"/\",",
            "@app.get(\"/cliente\",",
            "@app.get(\"/clientes\",",
            "@app.get(\"/SaaS\",",
            "@app.get(\"/admin\",",
            "@app.get(\"/login\",",
            "@app.get(\"/health\",",
            "@app.get(\"/{file_name:path}\",",
        ]
        for route in expected_routes:
            self.assertIn(route, content, f"Rota ausente: {route}")

    def test_04_path_traversal_sanitization_defense(self):
        def simulate_safe_asset_resolver(file_name: str, base_dir: Path):
            safe_name = os.path.basename(file_name.strip())
            if not safe_name: return None
            target_path = (base_dir / safe_name).resolve()
            if target_path.is_file() and str(target_path).startswith(str(base_dir)):
                return target_path
            return None

        self.assertIsNone(simulate_safe_asset_resolver("../../etc/passwd", FRONTEND_DIR))
        self.assertIsNone(simulate_safe_asset_resolver("..\\..\\windows\\cmd.exe", FRONTEND_DIR))
        valid = simulate_safe_asset_resolver("login.html", FRONTEND_DIR)
        self.assertIsNotNone(valid)
        self.assertEqual(valid.name, "login.html")

if __name__ == "__main__":
    unittest.main(verbosity=2)
