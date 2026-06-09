import unittest
import time
import numpy as np
from bridge import LiveAudioSource, GeminiLiveBridge, FRAME_SIZE

def generate_sine_wave(freq, sr, duration_ms, amplitude=8000):
    num_samples = int(sr * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, num_samples, False)
    wave = amplitude * np.sin(2 * np.pi * freq * t)
    return wave.astype(np.int16).tobytes()

class TestInterruptLatency(unittest.TestCase):
    def test_local_interrupt_latency(self):
        # Initialize components
        output_source = LiveAudioSource()
        bridge = GeminiLiveBridge(output_source=output_source)
        
        # simulate model output (24kHz mono)
        # 4 seconds of 1kHz sine wave
        model_output = generate_sine_wave(1000, 24000, 4000, amplitude=4000)
        output_source.feed(model_output)
        
        # Set bridge state to "model is talking"
        bridge._output_turn_open = True
        
        # 1. Feed 20ms of silence (16kHz mono)
        silence_20ms = generate_sine_wave(0, 16000, 20, amplitude=0)
        bridge.feed_audio(silence_20ms)
        
        # Verify NO local clear fires
        self.assertTrue(bridge._output_turn_open, "Local clear fired on silence!")
        
        # Wait 50ms
        time.sleep(0.05)
        
        # 2. Feed 20ms of speech-energy PCM (16kHz mono, 500Hz, 8000 amplitude)
        speech_20ms = generate_sine_wave(500, 16000, 20, amplitude=8000)
        
        start_time = time.perf_counter()
        bridge.feed_audio(speech_20ms)
        
        # Measure latency until read() returns empty or buffer empty
        # In this test, bridge.feed_audio calls output_source.clear()
        # Which empties the queue and buffer immediately.
        
        # We want to see how long it takes for the "system" to notice the clear.
        # Since we are in-process and synchronous, the call to feed_audio 
        # should have already called .clear().
        
        # Let's check the buffer size immediately.
        end_time = time.perf_counter()
        
        latency_ms = (end_time - start_time) * 1000
        
        print(f"\n[Test 1] Interrupt latency: {latency_ms:.3f} ms")
        
        # Verify the state was updated
        self.assertFalse(bridge._output_turn_open, "Local interrupt failed to close turn")
        # Verify the buffer is actually cleared
        # output_source.read() should return empty or silence if finished
        # but specifically, the .clear() method is what we are testing.
        # We check if the queue and buffer were cleared.
        self.assertEqual(len(output_source._buffer), 0, "Output buffer not cleared")
        self.assertTrue(output_source._q.empty(), "Output queue not emptied")
        
        self.assertLess(latency_ms, 100, f"Latency {latency_ms:.3f}ms exceeds target 100ms")

if __name__ == "__main__":
    unittest.main()
