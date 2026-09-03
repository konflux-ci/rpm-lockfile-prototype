import json
from io import StringIO
from unittest.mock import patch, Mock

import pytest
import jsonschema

from rpm_lockfile import schema


class TestValidate:
    """Test the validate function."""

    def test_valid_config(self):
        """Test validation of valid configuration."""
        valid_config = {
            "contentOrigin": {"repofiles": []},
            "packages": ["bash", "curl"],
        }

        with patch("rpm_lockfile.schema.get_schema") as mock_get_schema:
            mock_schema = {"type": "object", "properties": {}, "required": []}
            mock_get_schema.return_value = mock_schema

            with patch("jsonschema.validate") as mock_validate:
                schema.validate(valid_config)
                mock_validate.assert_called_once_with(valid_config, mock_schema)

    def test_invalid_config_exits(self):
        """Test that validation error causes system exit."""
        invalid_config = {"invalid": "config"}

        with patch("rpm_lockfile.schema.get_schema") as mock_get_schema:
            mock_schema = {"type": "object", "required": ["contentOrigin"]}
            mock_get_schema.return_value = mock_schema

            with patch("jsonschema.validate") as mock_validate:
                mock_validate.side_effect = jsonschema.ValidationError(
                    "Missing contentOrigin"
                )

                with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    with pytest.raises(SystemExit) as exc_info:
                        schema.validate(invalid_config)

                    assert exc_info.value.code == 1
                    assert "Missing contentOrigin" in mock_stderr.getvalue()

    def test_validation_error_handling(self):
        """Test proper handling of validation errors."""
        config = {"packages": "should be array"}

        # Use real schema validation to test error handling
        with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
            with pytest.raises(SystemExit):
                schema.validate(config)

            error_output = mock_stderr.getvalue()
            assert len(error_output) > 0  # Should contain error message


class TestHelpAction:
    """Test the HelpAction argparse action."""

    def test_help_action_init(self):
        """Test HelpAction initialization."""
        action = schema.HelpAction(["--print-schema"], dest="print_schema")
        assert action.nargs == 0

    def test_help_action_call(self):
        """Test HelpAction execution."""
        parser = Mock()
        namespace = Mock()

        action = schema.HelpAction(["--print-schema"], dest="print_schema")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            action(parser, namespace, None, "--print-schema")

            output = mock_stdout.getvalue()

            # Should output valid JSON schema
            schema_output = json.loads(output)
            assert "$schema" in schema_output
            assert "type" in schema_output

            # Should call parser.exit()
            parser.exit.assert_called_once()


class TestSchemaValidationIntegration:
    """Integration tests for schema validation with real data."""

    def test_minimal_valid_config(self):
        """Test minimal valid configuration."""
        config = {"contentOrigin": {"repofiles": []}}

        # This should not raise an exception
        schema.validate(config)

    def test_full_valid_config(self):
        """Test comprehensive valid configuration."""
        config = {
            "contentOrigin": {
                "repofiles": [{"location": "https://example.com/repo.repo"}]
            },
            "packages": [
                "bash",
                {"name": "gcc", "arches": {"only": ["x86_64", "aarch64"]}},
            ],
            "arches": ["x86_64"],
            "context": {"image": "registry.example.com/ubi9:latest"},
            "allowerasing": True,
            "noSources": False,
            "installWeakDeps": True,
        }

        # This should not raise an exception
        schema.validate(config)

    def test_invalid_packages_format(self):
        """Test invalid packages format."""
        config = {
            "contentOrigin": {"repofiles": []},
            "packages": "should be array",  # Invalid: should be array
        }

        with pytest.raises(SystemExit):
            schema.validate(config)

    def test_missing_content_origin(self):
        """Test missing required contentOrigin."""
        config = {
            "packages": ["bash"]
            # Missing contentOrigin
        }

        with pytest.raises(SystemExit):
            schema.validate(config)

    def test_invalid_context_combination(self):
        """Test invalid context property combination."""
        config = {
            "contentOrigin": {"repofiles": []},
            "context": {
                # Can't have both image and containerfile
                "image": "registry.example.com/ubi9:latest",
                "containerfile": "Containerfile",
            },
        }

        with pytest.raises(SystemExit):
            schema.validate(config)

    def test_valid_context_variations(self):
        """Test different valid context configurations."""
        base_config = {"contentOrigin": {"repofiles": []}}

        # Test image context
        config1 = base_config.copy()
        config1["context"] = {"image": "registry.example.com/ubi9:latest"}
        schema.validate(config1)

        # Test containerfile context
        config2 = base_config.copy()
        config2["context"] = {"containerfile": "Containerfile"}
        schema.validate(config2)

        # Test rpm-ostree context
        config3 = base_config.copy()
        config3["context"] = {"rpmOstreeTreefile": "treefile.yaml"}
        schema.validate(config3)

        # Test local system context
        config4 = base_config.copy()
        config4["context"] = {"localSystem": True}
        schema.validate(config4)

        # Test bare context
        config5 = base_config.copy()
        config5["context"] = {"bare": True}
        schema.validate(config5)

    def test_arch_specifications(self):
        """Test different architecture specifications in packages."""
        config = {
            "contentOrigin": {"repofiles": []},
            "packages": [
                {"name": "intel-ucode", "arches": {"only": "x86_64"}},
                {"name": "s390-tools", "arches": {"only": ["s390x"]}},
                {"name": "cross-platform-pkg", "arches": {"not": "armv7hl"}},
                {"name": "another-pkg", "arches": {"not": ["armv7hl", "i686"]}},
            ],
        }

        # Should validate successfully
        schema.validate(config)
