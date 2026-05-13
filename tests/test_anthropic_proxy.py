import json
import unittest

from anthropic_proxy import ResponsesToAnthropicSSE, anthropic_to_responses


def parse_sse(lines):
    events = []
    for frame in lines:
        event = None
        data = None
        for raw_line in frame.strip().splitlines():
            if raw_line.startswith("event: "):
                event = raw_line[7:]
            elif raw_line.startswith("data: "):
                data = json.loads(raw_line[6:])
        events.append((event, data))
    return events


class AnthropicProxyConversionTests(unittest.TestCase):
    def test_tool_result_blocks_are_preserved_for_responses_input(self):
        converted = anthropic_to_responses({
            "model": "gpt-5.5",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I will inspect it."},
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "Bash",
                            "input": {"command": "pwd"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_123",
                            "content": "E:\\Project\\AlphaClaude",
                        }
                    ],
                },
            ],
        })

        self.assertEqual(converted["input"][0]["role"], "assistant")
        self.assertEqual(converted["input"][1]["type"], "function_call")
        self.assertEqual(converted["input"][1]["call_id"], "toolu_123")
        self.assertEqual(converted["input"][1]["name"], "Bash")
        self.assertEqual(json.loads(converted["input"][1]["arguments"]), {"command": "pwd"})
        self.assertEqual(converted["input"][2], {
            "type": "function_call_output",
            "call_id": "toolu_123",
            "output": "E:\\Project\\AlphaClaude",
        })

    def test_text_stream_has_valid_anthropic_sse_order(self):
        converter = ResponsesToAnthropicSSE("gpt-5.5")
        lines = []
        lines.extend(converter.convert({"type": "response.created"}))
        lines.extend(converter.convert({"type": "response.output_text.delta", "delta": "hello"}))
        lines.extend(converter.convert({"type": "response.completed", "response": {"usage": {"output_tokens": 1}}}))
        events = parse_sse(lines)

        self.assertEqual([event for event, _ in events], [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ])
        self.assertEqual(events[2][1]["delta"], {"type": "text_delta", "text": "hello"})
        self.assertEqual(events[4][1]["delta"]["stop_reason"], "end_turn")

    def test_function_call_stream_becomes_tool_use(self):
        converter = ResponsesToAnthropicSSE("gpt-5.5")
        lines = []
        lines.extend(converter.convert({"type": "response.created"}))
        lines.extend(converter.convert({
            "type": "response.output_item.added",
            "item": {"type": "function_call", "call_id": "call_1", "name": "Bash"},
        }))
        lines.extend(converter.convert({
            "type": "response.function_call_arguments.delta",
            "delta": '{"command":"pwd"}',
        }))
        lines.extend(converter.convert({"type": "response.function_call_arguments.done"}))
        lines.extend(converter.convert({"type": "response.completed", "response": {"usage": {"output_tokens": 2}}}))
        events = parse_sse(lines)

        self.assertEqual(events[1][0], "content_block_start")
        self.assertEqual(events[1][1]["content_block"], {
            "type": "tool_use",
            "id": "call_1",
            "name": "Bash",
            "input": {},
        })
        self.assertEqual(events[2][1]["delta"], {
            "type": "input_json_delta",
            "partial_json": '{"command":"pwd"}',
        })
        self.assertEqual(events[4][1]["delta"]["stop_reason"], "tool_use")


if __name__ == "__main__":
    unittest.main()
