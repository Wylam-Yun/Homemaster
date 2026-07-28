# Third-Party Notices

## OpenHarness

HomeMaster contains OpenHarness-derived Skills, bundled Skill Markdown, default tools and supporting services,
together with substantial adaptations of the terminal output renderer and Feishu channel boundary. The former
vendored source snapshot was locked to OpenHarness commit
`9b2efd795c6aa09f88b0c257d269a9e518da6ae7`; its immutable historical hashes and test mapping are archived under
`plan/V2.0/archive/`. HomeMaster no longer distributes an `openharness` Python package.

MIT License

Copyright (c) 2025 OpenHarness Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## mem0

HomeMaster vendors the complete Python runtime from `mem0ai==2.0.13`, sourced from the official PyPI wheel with
SHA-256 `dff29057329370243d88bfccd367deba41c2fb1652f63225a23068cbdd1bc066`. The only source change makes
`mem0.__version__` fall back to `2.0.13` when the separate `mem0ai` distribution metadata is intentionally absent.
The full file inventory and per-file hashes are distributed with HomeMaster and stored under
`third_party/mem0ai-2.0.13/`.

Copyright (c) mem0 contributors

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance
with the License. You may obtain a copy of the License at `https://www.apache.org/licenses/LICENSE-2.0`.
Unless required by applicable law or agreed to in writing, software distributed under the License is distributed
on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License
for the specific language governing permissions and limitations under the License.

## Lark OpenAPI Python SDK

HomeMaster optionally depends on `lark-oapi` 1.7.1 for Feishu/Lark HTTP and WebSocket transport.

MIT License

Copyright (c) 2023 Lark Technologies Pte. Ltd.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
