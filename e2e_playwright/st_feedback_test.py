# Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. (2022-2024)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from playwright.sync_api import Locator, Page, expect

from e2e_playwright.conftest import ImageCompareFunction, wait_for_app_run
from e2e_playwright.shared.app_utils import get_markdown


def get_button_group(app: Page, index: int) -> Locator:
    return app.get_by_test_id("stButtonGroup").nth(index)


def get_feedback_icon_button(locator: Locator, type: str, index: int = 0) -> Locator:
    return (
        locator.get_by_test_id("baseButton-buttonGroup")
        .filter(has_text=type)
        .nth(index)
    )


def test_click_thumbsup_and_take_snapshot(
    app: Page, assert_snapshot: ImageCompareFunction
):
    thumbs = get_button_group(app, 0)
    get_feedback_icon_button(thumbs, "thumb_up").click()
    wait_for_app_run(app)
    assert_snapshot(thumbs, name="st_feedback-thumbs")


def test_clicking_on_faces_shows_sentiment_via_on_change_callback_and_take_snapshot(
    app: Page, assert_snapshot: ImageCompareFunction
):
    faces = get_button_group(app, 1)
    get_feedback_icon_button(faces, "sentiment_satisfied").click()
    wait_for_app_run(app)
    text = get_markdown(app, "Faces sentiment: 3")
    expect(text).to_be_attached()
    assert_snapshot(faces, name="st_feedback-faces")


def test_clicking_on_stars_shows_sentiment_and_take_snapshot(
    app: Page, assert_snapshot: ImageCompareFunction
):
    stars = get_button_group(app, 2)
    get_feedback_icon_button(stars, "star", 4).click()
    wait_for_app_run(app)
    text = get_markdown(app, "Star sentiment: 4")
    expect(text).to_be_attached()
    assert_snapshot(stars, name="st_feedback-stars")