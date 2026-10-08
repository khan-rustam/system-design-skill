import unittest
from unittest import mock


class CreateOrderValidationTest(unittest.TestCase):
    def setUp(self):
        env = {
            "DATABASE_URL": "postgresql://localhost/test",
            "PAYGATE_API_KEY": "k",
            "PAYGATE_WEBHOOK_SECRET": "s",
            "CARRIER_API_KEY": "c",
        }
        self.env = mock.patch.dict("os.environ", env)
        self.env.start()
        with mock.patch("app.db.init_pool"), mock.patch("app.BackgroundScheduler"):
            from app import create_app

            self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()

    @mock.patch("app.auth.query", return_value=[{"user_id": 7}])
    def test_rejects_quantity_over_limit(self, _auth):
        resp = self.client.post(
            "/orders",
            json={"product_id": 1, "address_id": 1, "quantity": 21},
            headers={"Authorization": "Bearer t"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_requires_auth(self):
        resp = self.client.get("/orders")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
