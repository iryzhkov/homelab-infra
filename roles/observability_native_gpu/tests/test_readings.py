#!/usr/bin/env python3
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "files/observability-native-gpu-report"
loader = importlib.machinery.SourceFileLoader("native_gpu", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class NativeGPUReadingsTest(unittest.TestCase):
    def test_native_nvidia_identity_units_and_unsupported_fields(self):
        device, values = module.nvidia_values("25, 34, 24564, 512, 33.2, 00000000:01:00.0")
        self.assertEqual(device, "0000:01:00.0")
        self.assertEqual(values["utilization_ratio"], .25)
        self.assertEqual(values["memory_used_bytes"], 512 * 1024 * 1024)
        self.assertEqual(values["power_watts"], 33.2)
        _, values = module.nvidia_values("0, [N/A], 24564, 0, [Not Supported], 00000000:01:00.0")
        self.assertEqual(values["utilization_ratio"], 0)
        self.assertNotIn("temperature_celsius", values)
        self.assertNotIn("power_watts", values)
        with self.assertRaises(ValueError):
            module.nvidia_values("101, 34, 1, 0, 1, 00000000:01:00.0")

    def test_native_amd_scaling_and_apu_power_is_not_gpu_power(self):
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory)
            for name, value in {"gpu_busy_percent": "17", "mem_info_vram_total": "536870912", "mem_info_vram_used": "1234"}.items():
                (device / name).write_text(value)
            hwmon = device / "hwmon/hwmon0"
            hwmon.mkdir(parents=True)
            (hwmon / "temp1_input").write_text("34500")
            (hwmon / "power1_average").write_text("12300000")
            (hwmon / "power1_label").write_text("PPT")
            values = module.amd_values(device)
            self.assertEqual(values["utilization_ratio"], .17)
            self.assertEqual(values["temperature_celsius"], 34.5)
            self.assertEqual(values["memory_total_bytes"], 536870912)
            self.assertNotIn("power_watts", values)
            (hwmon / "power1_label").write_text("GPU Power")
            self.assertAlmostEqual(module.amd_values(device)["power_watts"], 12.3)

    def test_failed_adapter_clears_readings_and_does_not_expose_errors(self):
        config = {"host": "gaming-pc", "devices": [
            {"physical_device": "0000:01:00.0", "vendor": "nvidia"},
            {"physical_device": "0000:0c:00.0", "vendor": "amd"}]}
        def read(item):
            if item["vendor"] == "nvidia":
                raise ValueError("PRIVATE_NATIVE_ERROR")
            return {"utilization_ratio": 0}
        text = module.samples(config, read=read, now=lambda: 123)
        self.assertIn('gpu_scrape_success{host="gaming-pc",device="0000:01:00.0"} 0', text)
        self.assertNotIn('gpu_utilization_ratio{host="gaming-pc",device="0000:01:00.0"}', text)
        self.assertNotIn("PRIVATE_NATIVE_ERROR", text)
        self.assertIn('gpu_last_success_timestamp_seconds{host="gaming-pc",device="0000:0c:00.0"} 123', text)

    def test_ambiguous_inventory_and_unsupported_apple_never_become_native_zero(self):
        item = {"physical_device": "0000:01:00.0", "vendor": "nvidia"}
        with self.assertRaises(ValueError):
            module.validate_config({"host": "gaming-pc", "devices": [item, item]})
        with self.assertRaises(ValueError):
            module.validate_config({"host": "laptop", "devices": [{"physical_device": "206400000.gpu", "vendor": "apple"}]})

    def test_atomic_output_replaces_whole_sample_and_cleans_owned_tempfile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native-gpu.prom"
            path.write_text("old unsafe sample")
            module.atomic_write(path, "new sample\n")
            self.assertEqual(path.read_text(), "new sample\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
