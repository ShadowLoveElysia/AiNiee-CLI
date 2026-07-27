import unittest

from ModuleFolders.Base.EventManager import EventManager


class EventManagerSnapshotTests(unittest.TestCase):
    def test_new_subscription_does_not_receive_in_flight_event(self):
        manager = EventManager()
        calls = []

        def late_handler(event, data):
            calls.append(("late", data["id"]))

        def first_handler(event, data):
            calls.append(("first", data["id"]))
            manager.unsubscribe(event, first_handler)
            manager.subscribe(event, late_handler)

        def second_handler(event, data):
            calls.append(("second", data["id"]))

        manager.subscribe(7, first_handler)
        manager.subscribe(7, second_handler)
        manager.emit(7, {"id": 1})

        self.assertEqual(
            calls,
            [("first", 1), ("second", 1)],
        )

        manager.emit(7, {"id": 2})
        self.assertEqual(
            calls,
            [
                ("first", 1),
                ("second", 1),
                ("second", 2),
                ("late", 2),
            ],
        )


if __name__ == "__main__":
    unittest.main()
