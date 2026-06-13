import unittest

import mini_call_server as server


class MediaBridgeTests(unittest.TestCase):
    def test_join_bridge_pairs_two_waiting_sessions(self):
        logger = server.SbcLogger(None)
        media = server.MediaServer("127.0.0.1", 12000, 12010, None, logger)
        first = server.RtpSession("call-a", "127.0.0.1", 12000, media_mode="bridge", bridge_id="bridge")
        second = server.RtpSession("call-b", "127.0.0.1", 12002, media_mode="bridge", bridge_id="bridge")

        media.join_bridge(first)
        self.assertIs(media.bridge_waiting["bridge"], first)

        media.join_bridge(second)
        self.assertIs(first.peer_session, second)
        self.assertIs(second.peer_session, first)
        self.assertNotIn("bridge", media.bridge_waiting)


if __name__ == "__main__":
    unittest.main()
