# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import base64
import html
import os
import urllib
from unittest.mock import patch

from odoo.exceptions import AccessDenied, UserError, ValidationError
from odoo.tests import HttpCase, tagged

from .fake_idp import DummyResponse, FakeIDP


@tagged("saml", "post_install", "-at_install")
class TestPySaml(HttpCase):
    def setUp(self):
        super().setUp()

        with open(
            os.path.join(os.path.dirname(__file__), "data", "sp.pem"),
            encoding="UTF-8",
        ) as file:
            sp_pem_public = file.read()

        with open(
            os.path.join(os.path.dirname(__file__), "data", "sp.key"),
            encoding="UTF-8",
        ) as file:
            sp_pem_private = file.read()

        self.saml_provider = self.env["auth.saml.provider"].create(
            {
                "name": "SAML Provider Demo",
                "idp_metadata": FakeIDP().get_metadata(),
                "sp_pem_public": base64.b64encode(sp_pem_public.encode()),
                "sp_pem_private": base64.b64encode(sp_pem_private.encode()),
                "body": "Login with Authentic",
                "active": True,
                "sig_alg": "SIG_RSA_SHA1",
                "matching_attribute": "mail",
            }
        )
        self.url_saml_request = (
            "/auth_saml/get_auth_request?pid=%d" % self.saml_provider.id
        )

        self.idp = FakeIDP([self.saml_provider._metadata_string()])

        # Create a user with only password, and another with both password and saml id
        self.user, self.user2 = (
            self.env["res.users"]
            .with_context(no_reset_password=True, tracking_disable=True)
            .create(
                [
                    {
                        "name": "User",
                        "email": "test@example.com",
                        "login": "test@example.com",
                        "password": "Lu,ums-7vRU>0i]=YDLa",
                    },
                    {
                        "name": "User with SAML",
                        "email": "user@example.com",
                        "login": "user@example.com",
                        "password": "NesTNSte9340D720te>/-A",
                        "saml_ids": [
                            (
                                0,
                                0,
                                {
                                    "saml_provider_id": self.saml_provider.id,
                                    "saml_uid": "user@example.com",
                                },
                            )
                        ],
                    },
                ]
            )
        )

    def test_ensure_provider_appears_on_login(self):
        # SAML provider should be listed in the login page
        response = self.url_open("/web/login")
        self.assertIn("Login with Authentic", response.text)
        self.assertIn(self.url_saml_request, response.text)

    def test_ensure_provider_appears_on_login_with_redirect_param(self):
        """Test that SAML provider is listed in the login page keeping the redirect"""
        response = self.url_open(
            "/web/login?redirect=%2Fweb%23action%3D37%26model%3Dir.module.module%26view"
            "_type%3Dkanban%26menu_id%3D5"
        )
        self.assertIn("Login with Authentic", response.text)
        self.assertIn(
            "/auth_saml/get_auth_request?pid={}&amp;redirect=%2Fweb%23action%3D37%26mod"
            "el%3Dir.module.module%26view_type%3Dkanban%26menu_id%3D5".format(
                self.saml_provider.id
            ),
            response.text,
        )

    def test__onchange_name(self):
        temp = self.saml_provider.body
        self.saml_provider.body = ""
        r = self.saml_provider._onchange_name()
        self.assertEqual(r, None)
        self.assertEqual(self.saml_provider.body, self.saml_provider.name)
        self.saml_provider.body = temp

    def test__compute_sp_metadata_url__new_provider(self):
        # Create a new unsaved record
        new_provider = self.env["auth.saml.provider"].new(
            {"name": "New SAML Provider", "sp_baseurl": "http://example.com"}
        )
        # Compute the metadata URL
        new_provider._compute_sp_metadata_url()
        # Assert that sp_metadata_url is False for the new record
        self.assertFalse(new_provider.sp_metadata_url)
        new_provider.unlink()

    def test__compute_sp_metadata_url__provider_has_sp_baseurl(self):
        # Create a new saved record with sp_baseurl set
        temp = self.saml_provider.sp_baseurl
        self.saml_provider.sp_baseurl = "http://example.com"
        self.saml_provider._compute_sp_metadata_url()
        expected_qs = urllib.parse.urlencode(
            {"p": self.saml_provider.id, "d": self.env.cr.dbname}
        )
        expected_url = urllib.parse.urljoin(
            "http://example.com", ("/auth_saml/metadata?%s" % expected_qs)
        )
        # Assert that sp_metadata_url is set correctly
        self.assertEqual(self.saml_provider.sp_metadata_url, expected_url)
        self.saml_provider.sp_baseurl = temp

    def _add_mapping_to_provider(self):
        """Add mapping to the provider"""
        self.saml_provider.attribute_mapping_ids = [
            (0, 0, {"attribute_name": "mail", "field_name": "login"}),
            (0, 0, {"attribute_name": "givenName", "field_name": "name"}),
            (
                0,
                0,
                {"attribute_name": "nick_name", "field_name": "name"},
            ),  # This attribute is not in attrs
        ]

    def test__hook_validate_auth_response(self):
        # Create a fake response with attributes
        fake_response = DummyResponse(200, "fake_data")
        fake_response.set_identity(
            {"mail": "new_user@example.com", "givenName": "New", "last_name": "User"}
        )
        self._add_mapping_to_provider()
        # Call the method
        result = self.saml_provider._hook_validate_auth_response(
            fake_response, "test@example.com"
        )

        # Check the result
        self.assertIn("mapped_attrs", result)
        self.assertEqual(result["mapped_attrs"]["login"], "new_user@example.com")
        self.assertEqual(result["mapped_attrs"]["name"], "New")
        self.assertNotIn("middle_name", result["mapped_attrs"])

    def test_get_config_for_provider(self):
        temp = self.saml_provider.sp_baseurl
        self.saml_provider.sp_baseurl = "http://example.com"
        self.saml_provider._get_config_for_provider(None)
        self.saml_provider.sp_baseurl = temp

    def test_ensure_metadata_present(self):
        response = self.url_open(
            "/auth_saml/metadata?p=%d&d=%s"
            % (self.saml_provider.id, self.env.cr.dbname)
        )

        self.assertTrue(response.ok)
        self.assertTrue("xml" in response.headers.get("Content-Type"))

    def test_ensure_get_auth_request_redirects(self):
        response = self.url_open(
            "/auth_saml/get_auth_request?pid=%d" % self.saml_provider.id,
            allow_redirects=False,
        )
        self.assertTrue(response.ok)
        self.assertEqual(response.status_code, 303)
        self.assertIn(
            "http://localhost:8000/sso/redirect?SAMLRequest=",
            response.headers.get("Location"),
        )

    def test_login_no_saml(self):
        """
        Login with a user account, but without any SAML provider setup
        against the user
        """
        # Standard login using password
        self.authenticate(user="test@example.com", password="Lu,ums-7vRU>0i]=YDLa")
        self.assertEqual(self.session.uid, self.user.id)

        self.logout()

        # Try to log in with a non-existing SAML token
        with self.assertRaises(AccessDenied):
            self.authenticate(user="test@example.com", password="test_saml_token")

        redirect_url = self.saml_provider._get_auth_request()
        self.assertIn("http://localhost:8000/sso/redirect?SAMLRequest=", redirect_url)

        response = self.idp.fake_login(redirect_url)
        self.assertEqual(200, response.status_code)
        unpacked_response = response._unpack()

        with self.assertRaises(AccessDenied):
            self.env["res.users"].sudo().auth_saml(
                self.saml_provider.id, unpacked_response.get("SAMLResponse"), None
            )

    def add_provider_to_user(self):
        """Add a provider to self.user"""
        self.user.write(
            {
                "saml_ids": [
                    (
                        0,
                        0,
                        {
                            "saml_provider_id": self.saml_provider.id,
                            "saml_uid": "test@example.com",
                        },
                    )
                ]
            }
        )

    def test_login_with_saml(self):
        self.add_provider_to_user()

        redirect_url = self.saml_provider._get_auth_request()
        self.assertIn("http://localhost:8000/sso/redirect?SAMLRequest=", redirect_url)

        response = self.idp.fake_login(redirect_url)
        self.assertEqual(200, response.status_code)
        unpacked_response = response._unpack()

        (database, login, token) = (
            self.env["res.users"]
            .sudo()
            .auth_saml(
                self.saml_provider.id, unpacked_response.get("SAMLResponse"), None
            )
        )

        self.assertEqual(database, self.env.cr.dbname)
        self.assertEqual(login, self.user.login)

        # We should not be able to log in with the wrong token
        with self.assertRaises(AccessDenied):
            self.authenticate(user="test@example.com", password=f"{token}-WRONG")

        # User should now be able to log in with the token
        self.authenticate(user="test@example.com", password=token)

    def test_login_with_saml_mapping_attributes(self):
        """Test login with SAML on a provider with mapping attributes"""
        self.assertEqual(self.user.name, "User")
        self.assertEqual(self.user.login, "test@example.com")
        self._add_mapping_to_provider()
        self.test_login_with_saml()
        # Changed due to mapping and FakeIDP returning another value
        self.assertEqual(self.user.name, "Test")
        # Not changed
        self.assertEqual(self.user.login, "test@example.com")

    def test_disallow_user_password_when_changing_ir_config_parameter(self):
        """Test that disabling users from having both a password and SAML ids remove
        users password."""
        # change the option
        self.browse_ref(
            "auth_saml.allow_saml_uid_and_internal_password"
        ).value = "False"
        # The password should be blank and the user should not be able to connect
        with self.assertRaises(AccessDenied):
            self.authenticate(
                user="user@example.com", password="NesTNSte9340D720te>/-A"
            )

    def test_disallow_user_password_new_user(self):
        """Test that a new user can not be set up with both password and SAML ids when
        the disallow option is set."""
        # change the option
        self.browse_ref(
            "auth_saml.allow_saml_uid_and_internal_password"
        ).value = "False"
        with self.assertRaises(UserError):
            self.env["res.users"].with_context(no_reset_password=True).create(
                {
                    "name": "New user with SAML",
                    "email": "user2@example.com",
                    "login": "user2@example.com",
                    "password": "NesTNSte9340D720te>/-A",
                    "saml_ids": [
                        (
                            0,
                            0,
                            {
                                "saml_provider_id": self.saml_provider.id,
                                "saml_uid": "user2",
                            },
                        )
                    ],
                }
            )

    def test_disallow_user_password_no_password_set(self):
        """Test that a new user with SAML ids can not have its password set up when the
        disallow option is set."""
        # change the option
        self.browse_ref(
            "auth_saml.allow_saml_uid_and_internal_password"
        ).value = "False"
        # Create a new user with only SAML ids
        user = (
            self.env["res.users"]
            .with_context(no_reset_password=True, tracking_disable=True)
            .create(
                {
                    "name": "New user with SAML",
                    "email": "user2@example.com",
                    "login": "user2@example.com",
                    "saml_ids": [
                        (
                            0,
                            0,
                            {
                                "saml_provider_id": self.saml_provider.id,
                                "saml_uid": "unused",
                            },
                        )
                    ],
                }
            )
        )
        # Assert that the user password can not be set
        with self.assertRaises(ValidationError):
            user.password = "new password"

    def test_disallow_user_password(self):
        """Test that existing user password is deleted when adding an SAML provider when
        the disallow option is set."""
        # change the option
        self.browse_ref(
            "auth_saml.allow_saml_uid_and_internal_password"
        ).value = "False"
        # Test that existing user password is deleted when adding an SAML provider
        self.authenticate(user="test@example.com", password="Lu,ums-7vRU>0i]=YDLa")
        self.add_provider_to_user()
        with self.assertRaises(AccessDenied):
            self.authenticate(user="test@example.com", password="Lu,ums-7vRU>0i]=YDLa")

    def test_disallow_user_admin_can_have_password(self):
        """Test that admin can have its password set
        even if the disallow option is set."""
        # change the option
        self.browse_ref(
            "auth_saml.allow_saml_uid_and_internal_password"
        ).value = "False"
        # Test base.user_admin exception
        self.env.ref("base.user_admin").password = "nNRST4j*->sEatNGg._!"

    def test_db_filtering(self):
        # change filter to only allow our db.
        with patch("odoo.http.db_filter", new=lambda *args, **kwargs: []):
            self.add_provider_to_user()

            redirect_url = self.saml_provider._get_auth_request()
            response = self.idp.fake_login(redirect_url)
            unpacked_response = response._unpack()

            for key in unpacked_response:
                unpacked_response[key] = html.unescape(unpacked_response[key])
            response = self.url_open("/auth_saml/signin", data=unpacked_response)
            self.assertFalse(response.ok)
            self.assertIn(response.status_code, [400, 404])

    def test_redirect_after_login(self):
        """Test that providing a redirect will be kept after SAML login."""
        self.add_provider_to_user()

        redirect_url = self.saml_provider._get_auth_request(
            {
                "r": "%2Fweb%23action%3D37%26model%3Dir.module.module%26view_type%3Dkan"
                "ban%26menu_id%3D5"
            }
        )
        response = self.idp.fake_login(redirect_url)
        unpacked_response = response._unpack()

        for key in unpacked_response:
            unpacked_response[key] = html.unescape(unpacked_response[key])
        response = self.url_open(
            "/auth_saml/signin",
            data=unpacked_response,
            allow_redirects=True,
            timeout=300,
        )
        self.assertTrue(response.ok)
        self.assertEqual(
            response.url,
            self.base_url()
            + "/web#action=37&model=ir.module.module&view_type=kanban&menu_id=5",
        )

    def test_disallow_user_password_when_changing_settings(self):
        """Test that disabling the setting will remove passwords from related users"""
        # We activate the settings to allow password login
        self.env["res.config.settings"].create(
            {
                "allow_saml_uid_and_internal_password": True,
            }
        ).execute()

        # Test the user can login with the password
        self.authenticate(user="user@example.com", password="NesTNSte9340D720te>/-A")

        self.env["res.config.settings"].create(
            {
                "allow_saml_uid_and_internal_password": False,
            }
        ).execute()

        with self.assertRaises(AccessDenied):
            self.authenticate(
                user="user@example.com", password="NesTNSte9340D720te>/-A"
            )
