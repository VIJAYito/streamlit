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

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from textwrap import dedent
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Callable,
    Final,
    Generic,
    Literal,
    TextIO,
    Union,
    cast,
)

from typing_extensions import TypeAlias

from streamlit import runtime
from streamlit.elements.form import current_form_id, is_in_form
from streamlit.elements.lib.policies import (
    check_cache_replay_rules,
    check_callback_rules,
    check_fragment_path_policy,
    check_session_state_rules,
)
from streamlit.errors import StreamlitAPIException
from streamlit.file_util import get_main_script_directory, normalize_path_join
from streamlit.navigation.page import StreamlitPage
from streamlit.proto.Button_pb2 import Button as ButtonProto
from streamlit.proto.ButtonGroup_pb2 import ButtonGroup as ButtonGroupProto
from streamlit.proto.DownloadButton_pb2 import DownloadButton as DownloadButtonProto
from streamlit.proto.LinkButton_pb2 import LinkButton as LinkButtonProto
from streamlit.proto.PageLink_pb2 import PageLink as PageLinkProto
from streamlit.runtime.metrics_util import gather_metrics
from streamlit.runtime.scriptrunner import ScriptRunContext, get_script_run_ctx
from streamlit.runtime.state import (
    WidgetArgs,
    WidgetCallback,
    WidgetKwargs,
    register_widget,
)
from streamlit.runtime.state.common import T, compute_widget_id, save_for_app_testing
from streamlit.string_util import validate_icon_or_emoji
from streamlit.type_util import Key, to_key
from streamlit.url_util import is_url

if TYPE_CHECKING:
    from streamlit.delta_generator import DeltaGenerator

FORM_DOCS_INFO: Final = """

For more information, refer to the
[documentation for forms](https://docs.streamlit.io/develop/api-reference/execution-flow/st.form).
"""

DownloadButtonDataType: TypeAlias = Union[str, bytes, TextIO, BinaryIO, io.RawIOBase]


@dataclass
class ButtonSerde:
    def serialize(self, v: bool) -> bool:
        return bool(v)

    def deserialize(self, ui_value: bool | None, widget_id: str = "") -> bool:
        return ui_value or False


class ButtonGroupSerde(Generic[T]):
    def serialize(self, value: T | None) -> list[int]:
        raise NotImplementedError

    def deserialize(self, ui_value: list[int], widget_id: str = "") -> T | None:
        raise NotImplementedError


@dataclass
class ButtonGroupOptionsSerde(ButtonGroupSerde[list[str]]):
    """For button-mode acts as a trigger value, for radio button and checkbox mode acts
    as a persistent value serializer/deserializer.
    """

    options: list[str]
    default_value: list[int]
    # mode: ButtonGroupProto.ClickMode.ValueType

    def serialize(self, value: list[str] | None) -> list[int]:
        if value is None:
            return []
        return [self.options.index(val) for val in value]

    def deserialize(self, ui_value: list[int], widget_id: str = "") -> list[str] | None:
        current_value: list[int] = (
            ui_value if ui_value is not None else self.default_value
        )
        return [self.options[val] for val in current_value]


@dataclass
class ButtonGroupFeedbackSerde(ButtonGroupSerde[int]):
    index_to_score_mapping: list[int] | None

    def serialize(self, value: int | None) -> list[int]:
        if value is None:
            return []
        return [value]

    def deserialize(
        self, ui_value: list[int] | None, widget_id: str = ""
    ) -> int | None:
        if ui_value is None:
            return None

        if self.index_to_score_mapping is None:
            return ui_value[0]

        return self.index_to_score_mapping[ui_value[0]]


class ButtonMixin:
    @gather_metrics("button")
    def button(
        self,
        label: str,
        key: Key | None = None,
        help: str | None = None,
        on_click: WidgetCallback | None = None,
        args: WidgetArgs | None = None,
        kwargs: WidgetKwargs | None = None,
        *,  # keyword-only arguments:
        type: Literal["primary", "secondary"] = "secondary",
        disabled: bool = False,
        use_container_width: bool = False,
    ) -> bool:
        r"""Display a button widget.

        Parameters
        ----------
        label : str
            A short label explaining to the user what this button is for.
            The label can optionally contain Markdown and supports the following
            elements: Bold, Italics, Strikethroughs, Inline Code, and Emojis.

            This also supports:

            * Emoji shortcodes, such as ``:+1:``  and ``:sunglasses:``.
              For a list of all supported codes,
              see https://share.streamlit.io/streamlit/emoji-shortcodes.

            * LaTeX expressions, by wrapping them in "$" or "$$" (the "$$"
              must be on their own lines). Supported LaTeX functions are listed
              at https://katex.org/docs/supported.html.

            * Colored text and background colors for text, using the syntax
              ``:color[text to be colored]`` and ``:color-background[text to be colored]``,
              respectively. ``color`` must be replaced with any of the following
              supported colors: blue, green, orange, red, violet, gray/grey, rainbow.
              For example, you can use ``:orange[your text here]`` or
              ``:blue-background[your text here]``.

            Unsupported elements are unwrapped so only their children (text contents) render.
            Display unsupported elements as literal characters by
            backslash-escaping them. E.g. ``1\. Not an ordered list``.
        key : str or int
            An optional string or integer to use as the unique key for the widget.
            If this is omitted, a key will be generated for the widget
            based on its content. Multiple widgets of the same type may
            not share the same key.
        help : str
            An optional tooltip that gets displayed when the button is
            hovered over.
        on_click : callable
            An optional callback invoked when this button is clicked.
        args : tuple
            An optional tuple of args to pass to the callback.
        kwargs : dict
            An optional dict of kwargs to pass to the callback.
        type : "secondary" or "primary"
            An optional string that specifies the button type. Can be "primary" for a
            button with additional emphasis or "secondary" for a normal button. Defaults
            to "secondary".
        disabled : bool
            An optional boolean, which disables the button if set to True. The
            default is False.
        use_container_width : bool
            Whether to expand the button's width to fill its parent container.
            If ``use_container_width`` is ``False`` (default), Streamlit sizes
            the button to fit its contents. If ``use_container_width`` is
            ``True``, the width of the button matches its parent container.

            In both cases, if the contents of the button are wider than the
            parent container, the contents will line wrap.

        Returns
        -------
        bool
            True if the button was clicked on the last run of the app,
            False otherwise.

        Example
        -------
        >>> import streamlit as st
        >>>
        >>> st.button("Reset", type="primary")
        >>> if st.button("Say hello"):
        ...     st.write("Why hello there")
        ... else:
        ...     st.write("Goodbye")

        .. output::
           https://doc-buton.streamlit.app/
           height: 220px

        """
        key = to_key(key)
        ctx = get_script_run_ctx()

        # Checks whether the entered button type is one of the allowed options - either "primary" or "secondary"
        if type not in ["primary", "secondary"]:
            raise StreamlitAPIException(
                'The type argument to st.button must be "primary" or "secondary". \n'
                f'The argument passed was "{type}".'
            )

        return self.dg._button(
            label,
            key,
            help,
            is_form_submitter=False,
            on_click=on_click,
            args=args,
            kwargs=kwargs,
            disabled=disabled,
            type=type,
            use_container_width=use_container_width,
            ctx=ctx,
        )

    @gather_metrics("download_button")
    def download_button(
        self,
        label: str,
        data: DownloadButtonDataType,
        file_name: str | None = None,
        mime: str | None = None,
        key: Key | None = None,
        help: str | None = None,
        on_click: WidgetCallback | None = None,
        args: WidgetArgs | None = None,
        kwargs: WidgetKwargs | None = None,
        *,  # keyword-only arguments:
        type: Literal["primary", "secondary"] = "secondary",
        disabled: bool = False,
        use_container_width: bool = False,
    ) -> bool:
        r"""Display a download button widget.

        This is useful when you would like to provide a way for your users
        to download a file directly from your app.

        Note that the data to be downloaded is stored in-memory while the
        user is connected, so it's a good idea to keep file sizes under a
        couple hundred megabytes to conserve memory.

        If you want to prevent your app from rerunning when a user clicks the
        download button, wrap the download button in a `fragment
        <https://docs.streamlit.io/develop/concepts/architecture/fragments>`_.

        Parameters
        ----------
        label : str
            A short label explaining to the user what this button is for.
            The label can optionally contain Markdown and supports the following
            elements: Bold, Italics, Strikethroughs, Inline Code, and Emojis.

            This also supports:

            * Emoji shortcodes, such as ``:+1:``  and ``:sunglasses:``.
              For a list of all supported codes,
              see https://share.streamlit.io/streamlit/emoji-shortcodes.

            * LaTeX expressions, by wrapping them in "$" or "$$" (the "$$"
              must be on their own lines). Supported LaTeX functions are listed
              at https://katex.org/docs/supported.html.

            * Colored text and background colors for text, using the syntax
              ``:color[text to be colored]`` and ``:color-background[text to be colored]``,
              respectively. ``color`` must be replaced with any of the following
              supported colors: blue, green, orange, red, violet, gray/grey, rainbow.
              For example, you can use ``:orange[your text here]`` or
              ``:blue-background[your text here]``.

            Unsupported elements are unwrapped so only their children (text contents)
            render. Display unsupported elements as literal characters by
            backslash-escaping them. E.g. ``1\. Not an ordered list``.
        data : str or bytes or file
            The contents of the file to be downloaded. See example below for
            caching techniques to avoid recomputing this data unnecessarily.
        file_name: str
            An optional string to use as the name of the file to be downloaded,
            such as 'my_file.csv'. If not specified, the name will be
            automatically generated.
        mime : str or None
            The MIME type of the data. If None, defaults to "text/plain"
            (if data is of type *str* or is a textual *file*) or
            "application/octet-stream" (if data is of type *bytes* or is a
            binary *file*).
        key : str or int
            An optional string or integer to use as the unique key for the widget.
            If this is omitted, a key will be generated for the widget
            based on its content. Multiple widgets of the same type may
            not share the same key.
        help : str
            An optional tooltip that gets displayed when the button is
            hovered over.
        on_click : callable
            An optional callback invoked when this button is clicked.
        args : tuple
            An optional tuple of args to pass to the callback.
        kwargs : dict
            An optional dict of kwargs to pass to the callback.
        type : "secondary" or "primary"
            An optional string that specifies the button type. Can be "primary" for a
            button with additional emphasis or "secondary" for a normal button. Defaults
            to "secondary".
        disabled : bool
            An optional boolean, which disables the download button if set to
            True. The default is False.
        use_container_width : bool
            Whether to expand the button's width to fill its parent container.
            If ``use_container_width`` is ``False`` (default), Streamlit sizes
            the button to fit its contents. If ``use_container_width`` is
            ``True``, the width of the button matches its parent container.

            In both cases, if the contents of the button are wider than the
            parent container, the contents will line wrap.

        Returns
        -------
        bool
            True if the button was clicked on the last run of the app,
            False otherwise.

        Examples
        --------
        Download a large DataFrame as a CSV:

        >>> import streamlit as st
        >>>
        >>> @st.cache_data
        ... def convert_df(df):
        ...     # IMPORTANT: Cache the conversion to prevent computation on every rerun
        ...     return df.to_csv().encode("utf-8")
        >>>
        >>> csv = convert_df(my_large_df)
        >>>
        >>> st.download_button(
        ...     label="Download data as CSV",
        ...     data=csv,
        ...     file_name="large_df.csv",
        ...     mime="text/csv",
        ... )

        Download a string as a file:

        >>> import streamlit as st
        >>>
        >>> text_contents = '''This is some text'''
        >>> st.download_button("Download some text", text_contents)

        Download a binary file:

        >>> import streamlit as st
        >>>
        >>> binary_contents = b"example content"
        >>> # Defaults to "application/octet-stream"
        >>> st.download_button("Download binary file", binary_contents)

        Download an image:

        >>> import streamlit as st
        >>>
        >>> with open("flower.png", "rb") as file:
        ...     btn = st.download_button(
        ...             label="Download image",
        ...             data=file,
        ...             file_name="flower.png",
        ...             mime="image/png"
        ...           )

        .. output::
           https://doc-download-buton.streamlit.app/
           height: 335px

        """
        ctx = get_script_run_ctx()

        if type not in ["primary", "secondary"]:
            raise StreamlitAPIException(
                'The type argument to st.button must be "primary" or "secondary". \n'
                f'The argument passed was "{type}".'
            )

        return self._download_button(
            label=label,
            data=data,
            file_name=file_name,
            mime=mime,
            key=key,
            help=help,
            on_click=on_click,
            args=args,
            kwargs=kwargs,
            disabled=disabled,
            type=type,
            use_container_width=use_container_width,
            ctx=ctx,
        )

    @gather_metrics("link_button")
    def link_button(
        self,
        label: str,
        url: str,
        *,
        help: str | None = None,
        type: Literal["primary", "secondary"] = "secondary",
        disabled: bool = False,
        use_container_width: bool = False,
    ) -> DeltaGenerator:
        r"""Display a link button element.

        When clicked, a new tab will be opened to the specified URL. This will
        create a new session for the user if directed within the app.

        Parameters
        ----------
        label : str
            A short label explaining to the user what this button is for.
            The label can optionally contain Markdown and supports the following
            elements: Bold, Italics, Strikethroughs, Inline Code, and Emojis.

            This also supports:

            * Emoji shortcodes, such as ``:+1:``  and ``:sunglasses:``.
              For a list of all supported codes,
              see https://share.streamlit.io/streamlit/emoji-shortcodes.

            * LaTeX expressions, by wrapping them in "$" or "$$" (the "$$"
              must be on their own lines). Supported LaTeX functions are listed
              at https://katex.org/docs/supported.html.

            * Colored text and background colors for text, using the syntax
              ``:color[text to be colored]`` and ``:color-background[text to be colored]``,
              respectively. ``color`` must be replaced with any of the following
              supported colors: blue, green, orange, red, violet, gray/grey, rainbow.
              For example, you can use ``:orange[your text here]`` or
              ``:blue-background[your text here]``.

            Unsupported elements are unwrapped so only their children (text contents)
            render. Display unsupported elements as literal characters by
            backslash-escaping them. E.g. ``1\. Not an ordered list``.
        url : str
            The url to be opened on user click
        help : str
            An optional tooltip that gets displayed when the button is
            hovered over.
        type : "secondary" or "primary"
            An optional string that specifies the button type. Can be "primary" for a
            button with additional emphasis or "secondary" for a normal button. Defaults
            to "secondary".
        disabled : bool
            An optional boolean, which disables the link button if set to
            True. The default is False.
        use_container_width : bool
            Whether to expand the button's width to fill its parent container.
            If ``use_container_width`` is ``False`` (default), Streamlit sizes
            the button to fit its contents. If ``use_container_width`` is
            ``True``, the width of the button matches its parent container.

            In both cases, if the contents of the button are wider than the
            parent container, the contents will line wrap.

        Example
        -------
        >>> import streamlit as st
        >>>
        >>> st.link_button("Go to gallery", "https://streamlit.io/gallery")

        .. output::
           https://doc-link-button.streamlit.app/
           height: 200px

        """
        # Checks whether the entered button type is one of the allowed options - either "primary" or "secondary"
        if type not in ["primary", "secondary"]:
            raise StreamlitAPIException(
                'The type argument to st.link_button must be "primary" or "secondary". '
                f'\nThe argument passed was "{type}".'
            )

        return self._link_button(
            label=label,
            url=url,
            help=help,
            disabled=disabled,
            type=type,
            use_container_width=use_container_width,
        )

    @gather_metrics("page_link")
    def page_link(
        self,
        page: str | StreamlitPage,
        *,
        label: str | None = None,
        icon: str | None = None,
        help: str | None = None,
        disabled: bool = False,
        use_container_width: bool | None = None,
    ) -> DeltaGenerator:
        r"""Display a link to another page in a multipage app or to an external page.

        If another page in a multipage app is specified, clicking ``st.page_link``
        stops the current page execution and runs the specified page as if the
        user clicked on it in the sidebar navigation.

        If an external page is specified, clicking ``st.page_link`` opens a new
        tab to the specified page. The current script run will continue if not
        complete.

        Parameters
        ----------
        page : str or st.Page
            The file path (relative to the main script) or an st.Page indicating
            the page to switch to. Alternatively, this can be the URL to an
            external page (must start with "http://" or "https://").
        label : str
            The label for the page link. Labels are required for external pages.
            Labels can optionally contain Markdown and supports the following
            elements: Bold, Italics, Strikethroughs, Inline Code, and Emojis.

            This also supports:

            * Emoji shortcodes, such as ``:+1:``  and ``:sunglasses:``.
              For a list of all supported codes,
              see https://share.streamlit.io/streamlit/emoji-shortcodes.

            * LaTeX expressions, by wrapping them in "$" or "$$" (the "$$"
              must be on their own lines). Supported LaTeX functions are listed
              at https://katex.org/docs/supported.html.

            * Colored text and background colors for text, using the syntax
              ``:color[text to be colored]`` and ``:color-background[text to be colored]``,
              respectively. ``color`` must be replaced with any of the following
              supported colors: blue, green, orange, red, violet, gray/grey, rainbow.
              For example, you can use ``:orange[your text here]`` or
              ``:blue-background[your text here]``.

            Unsupported elements are unwrapped so only their children (text contents)
            render. Display unsupported elements as literal characters by
            backslash-escaping them. E.g. ``1\. Not an ordered list``.
        icon : str, None
            An optional emoji or icon to display next to the button label. If ``icon``
            is ``None`` (default), no icon is displayed. If ``icon`` is a
            string, the following options are valid:

            * A single-character emoji. For example, you can set ``icon="🚨"``
              or ``icon="🔥"``. Emoji short codes are not supported.

            * An icon from the Material Symbols library (outlined style) in the
              format ``":material/icon_name:"`` where "icon_name" is the name
              of the icon in snake case.

              For example, ``icon=":material/thumb_up:"`` will display the
              Thumb Up icon. Find additional icons in the `Material Symbols \
              <https://fonts.google.com/icons?icon.set=Material+Symbols&icon.style=Outlined>`_
              font library.
        help : str
            An optional tooltip that gets displayed when the link is
            hovered over.
        disabled : bool
            An optional boolean, which disables the page link if set to
            ``True``. The default is ``False``.
        use_container_width : bool
            Whether to expand the link's width to fill its parent container.
            The default is ``True`` for page links in the sidebar and ``False``
            for those in the main app.

        Example
        -------
        Consider the following example given this file structure:

        >>> your-repository/
        >>> ├── pages/
        >>> │   ├── page_1.py
        >>> │   └── page_2.py
        >>> └── your_app.py

        >>> import streamlit as st
        >>>
        >>> st.page_link("your_app.py", label="Home", icon="🏠")
        >>> st.page_link("pages/page_1.py", label="Page 1", icon="1️⃣")
        >>> st.page_link("pages/page_2.py", label="Page 2", icon="2️⃣", disabled=True)
        >>> st.page_link("http://www.google.com", label="Google", icon="🌎")

        The default navigation is shown here for comparison, but you can hide
        the default navigation using the |client.showSidebarNavigation|_
        configuration option. This allows you to create custom, dynamic
        navigation menus for your apps!

        .. |client.showSidebarNavigation| replace:: ``client.showSidebarNavigation``
        .. _client.showSidebarNavigation: https://docs.streamlit.io/develop/api-reference/configuration/config.toml#client

        .. output ::
            https://doc-page-link.streamlit.app/
            height: 350px

        """

        return self._page_link(
            page=page,
            label=label,
            icon=icon,
            help=help,
            disabled=disabled,
            use_container_width=use_container_width,
        )

    @gather_metrics("button_group")
    def button_group(
        self,
        options: list[str],
        *,
        key: Key | None = None,
        default: list[bool] | None = None,
        click_mode: Literal["button", "checkbox", "radio"] = "button",
        disabled: bool = False,
        use_container_width: bool = False,
        format_func: Callable[[str], str] = lambda x: x,
        on_click: WidgetCallback | None = None,
        args: Any | None = None,
        kwargs: Any | None = None,
    ) -> str | list[str] | None:
        return self._button_group(
            options,
            key=key,
            default=default,
            click_mode=click_mode,
            disabled=disabled,
            use_container_width=use_container_width,
            format_func=format_func,
            on_click=on_click,
            args=args,
            kwargs=kwargs,
            serde=ButtonGroupOptionsSerde(options, []),
        )

    @gather_metrics("feedback")
    def feedback(
        self,
        options: Literal["thumbs", "smiles"] = "thumbs",
        *,
        key: str | None = None,
        disabled: bool = False,
        on_click: WidgetCallback | None = None,
        args: Any | None = None,
        kwargs: Any | None = None,
    ) -> int | None:
        def format_func_thumbs(option: str):
            if option == "thumbs_up":
                return ":material/thumb_up:"
            if option == "thumbs_down":
                return ":material/thumb_down:"
            return ""

        def format_func_smiles(option: str):
            if option == "sad":
                return ":material/sentiment_sad:"
            if option == "disappointed":
                return ":material/sentiment_dissatisfied:"
            if option == "neutral":
                return ":material/sentiment_neutral:"
            if option == "happy":
                return ":material/sentiment_satisfied:"
            if option == "very_happy":
                return ":material/sentiment_very_satisfied:"

            return ""

        index_to_score_mapping: list[int] | None = None
        if options == "thumbs":
            format_func = format_func_thumbs
            # thumbs_up is 1, thumbs_down is 0; but we want to show thumbs_up first, so its index is 0
            index_to_score_mapping = [1, 0]
        elif options == "smiles":
            format_func = format_func_smiles
        else:
            raise StreamlitAPIException(
                "The options argument to st.feedback must be 'thumbs' or 'smiles'. "
                f"The argument passed was '{options}'."
            )

        actual_options = (
            ["thumbs_up", "thumbs_down"]
            if options == "thumbs"
            else ["sad", "disappointed", "neutral", "happy", "very_happy"]
        )

        def _on_click(*args, **kwargs):
            if key is None:
                on_click(*args, **kwargs)
                return

            # Problem: we don't have the widget id here, so the key has to be provided
            ctx = get_script_run_ctx()
            current_value = (
                ctx.session_state[key]
                if key is not None and key in ctx.session_state
                else None
            )
            new_kwargs = dict(kwargs, _st_value=current_value)
            on_click(*args, **new_kwargs)

        return self._button_group(
            actual_options,
            key=key,
            click_mode="radio",
            disabled=disabled,
            format_func=format_func,
            on_click=_on_click if on_click else None,
            args=args,
            kwargs=kwargs,
            serde=ButtonGroupFeedbackSerde(index_to_score_mapping),
        )

    # @overload
    # def _button_group(
    #     self,
    #     options: str | list[str],
    #     *,
    #     key: Key | None = None,
    #     default: bool | list[bool] = False,
    #     click_mode: Literal["button", "checkbox", "radio"] = "button",
    #     disabled: bool = False,
    #     use_container_width: bool = False,
    #     format_func: Callable[[str], str] = lambda x: x,
    #     on_click: WidgetCallback | None = None,
    #     args: Any | None = None,
    #     kwargs: Any | None = None,
    #     serde: ButtonGroupFeedbackSerde,
    # ) -> list[int] | None: ...

    def _button_group(
        self,
        options: str | list[str],
        *,
        key: Key | None = None,
        default: list[bool] | None = None,
        click_mode: Literal["button", "checkbox", "radio"] = "button",
        disabled: bool = False,
        use_container_width: bool = False,
        format_func: Callable[[str], str] = lambda x: x,
        on_click: WidgetCallback | None = None,
        args: Any | None = None,
        kwargs: Any | None = None,
        serde: ButtonGroupSerde[T],
    ) -> T | None:
        if default is None:
            default = []
        key = to_key(key)

        check_fragment_path_policy(self.dg)
        check_cache_replay_rules()
        check_callback_rules(self.dg, on_change=on_click)
        check_session_state_rules(default_value=default, key=key)

        widget_name = "button_group"
        ctx = get_script_run_ctx()

        # TODO: convert default to indices

        options = [options] if isinstance(options, str) else options
        formatted_options = [format_func(o).encode("utf-8") for o in options]

        widget_id = compute_widget_id(
            widget_name,
            user_key=key,
            key=key,
            options=formatted_options,
            default=default,
            use_container_width=use_container_width,
            page=ctx.active_script_hash if ctx else None,
        )

        # if isinstance(default, list):
        default_values = [
            index for index, default_val in enumerate(default) if default_val is True
        ]
        # else:
        #     default_values = [int(default)] if default is True else []

        button_group_proto = ButtonGroupProto()
        button_group_proto.id = widget_id
        button_group_proto.options[:] = formatted_options
        button_group_proto.default[:] = default_values

        button_group_proto.disabled = disabled
        button_group_proto.click_mode = (
            ButtonGroupProto.BUTTON
            if click_mode == "button"
            else ButtonGroupProto.RADIO
            if click_mode == "radio"
            else ButtonGroupProto.CHECKBOX
        )
        button_group_proto.use_container_width = use_container_width
        # serde = ButtonGroupSerde(options, default_values, button_group_proto.click_mode)

        button_group_state = register_widget(
            widget_name,
            button_group_proto,
            # user_key=key,
            on_change_handler=on_click,
            args=args,
            kwargs=kwargs,
            deserializer=serde.deserialize,
            serializer=serde.serialize,
            ctx=ctx,
        )

        # TODO: add app_testing call?
        self.dg._enqueue(widget_name, button_group_proto)

        return button_group_state.value

    def _download_button(
        self,
        label: str,
        data: DownloadButtonDataType,
        file_name: str | None = None,
        mime: str | None = None,
        key: Key | None = None,
        help: str | None = None,
        on_click: WidgetCallback | None = None,
        args: WidgetArgs | None = None,
        kwargs: WidgetKwargs | None = None,
        *,  # keyword-only arguments:
        type: Literal["primary", "secondary"] = "secondary",
        disabled: bool = False,
        use_container_width: bool = False,
        ctx: ScriptRunContext | None = None,
    ) -> bool:
        key = to_key(key)

        check_cache_replay_rules()
        check_session_state_rules(default_value=None, key=key, writes_allowed=False)
        check_callback_rules(self.dg, on_click)

        id = compute_widget_id(
            "download_button",
            user_key=key,
            label=label,
            file_name=file_name,
            mime=mime,
            key=key,
            help=help,
            type=type,
            use_container_width=use_container_width,
            page=ctx.active_script_hash if ctx else None,
        )

        if is_in_form(self.dg):
            raise StreamlitAPIException(
                f"`st.download_button()` can't be used in an `st.form()`.{FORM_DOCS_INFO}"
            )

        download_button_proto = DownloadButtonProto()
        download_button_proto.id = id
        download_button_proto.use_container_width = use_container_width
        download_button_proto.label = label
        download_button_proto.default = False
        download_button_proto.type = type
        marshall_file(
            self.dg._get_delta_path_str(), data, download_button_proto, mime, file_name
        )
        download_button_proto.disabled = disabled

        if help is not None:
            download_button_proto.help = dedent(help)

        serde = ButtonSerde()

        button_state = register_widget(
            "download_button",
            download_button_proto,
            user_key=key,
            on_change_handler=on_click,
            args=args,
            kwargs=kwargs,
            deserializer=serde.deserialize,
            serializer=serde.serialize,
            ctx=ctx,
        )

        self.dg._enqueue("download_button", download_button_proto)
        return button_state.value

    def _link_button(
        self,
        label: str,
        url: str,
        help: str | None,
        *,  # keyword-only arguments:
        type: Literal["primary", "secondary"] = "secondary",
        disabled: bool = False,
        use_container_width: bool = False,
    ) -> DeltaGenerator:
        link_button_proto = LinkButtonProto()
        link_button_proto.label = label
        link_button_proto.url = url
        link_button_proto.type = type
        link_button_proto.use_container_width = use_container_width
        link_button_proto.disabled = disabled

        if help is not None:
            link_button_proto.help = dedent(help)

        return self.dg._enqueue("link_button", link_button_proto)

    def _page_link(
        self,
        page: str | StreamlitPage,
        *,  # keyword-only arguments:
        label: str | None = None,
        icon: str | None = None,
        help: str | None = None,
        disabled: bool = False,
        use_container_width: bool | None = None,
    ) -> DeltaGenerator:
        page_link_proto = PageLinkProto()
        page_link_proto.disabled = disabled

        if label is not None:
            page_link_proto.label = label

        if icon is not None:
            page_link_proto.icon = validate_icon_or_emoji(icon)

        if help is not None:
            page_link_proto.help = dedent(help)

        if use_container_width is not None:
            page_link_proto.use_container_width = use_container_width

        if isinstance(page, StreamlitPage):
            page_link_proto.page_script_hash = page._script_hash
            page_link_proto.page = page.url_path
            if label is None:
                page_link_proto.label = page.title
        else:
            # Handle external links:
            if is_url(page):
                if label is None or label == "":
                    raise StreamlitAPIException(
                        "The label param is required for external links used with st.page_link - please provide a label."
                    )
                else:
                    page_link_proto.page = page
                    page_link_proto.external = True
                    return self.dg._enqueue("page_link", page_link_proto)

            ctx = get_script_run_ctx()
            ctx_main_script = ""
            all_app_pages = {}
            if ctx:
                ctx_main_script = ctx.main_script_path
                all_app_pages = ctx.pages_manager.get_pages()

            main_script_directory = get_main_script_directory(ctx_main_script)
            requested_page = os.path.realpath(
                normalize_path_join(main_script_directory, page)
            )

            # Handle retrieving the page_script_hash & page
            for page_data in all_app_pages.values():
                full_path = page_data["script_path"]
                page_name = page_data["page_name"]
                if requested_page == full_path:
                    if label is None:
                        page_link_proto.label = page_name.replace("_", " ")
                    page_link_proto.page_script_hash = page_data["page_script_hash"]
                    page_link_proto.page = page_name
                    break

        if page_link_proto.page_script_hash == "":
            raise StreamlitAPIException(
                f"Could not find page: `{page}`. Must be the file path relative to the main script, from the directory: `{os.path.basename(main_script_directory)}`. Only the main app file and files in the `pages/` directory are supported."
            )

        return self.dg._enqueue("page_link", page_link_proto)

    def _button(
        self,
        label: str,
        key: str | None,
        help: str | None,
        is_form_submitter: bool,
        on_click: WidgetCallback | None = None,
        args: WidgetArgs | None = None,
        kwargs: WidgetKwargs | None = None,
        *,  # keyword-only arguments:
        type: Literal["primary", "secondary"] = "secondary",
        disabled: bool = False,
        use_container_width: bool = False,
        ctx: ScriptRunContext | None = None,
    ) -> bool:
        key = to_key(key)

        check_fragment_path_policy(self.dg)
        if not is_form_submitter:
            check_callback_rules(self.dg, on_click)
        check_cache_replay_rules()
        check_session_state_rules(default_value=None, key=key, writes_allowed=False)

        id = compute_widget_id(
            "button",
            user_key=key,
            label=label,
            key=key,
            help=help,
            is_form_submitter=is_form_submitter,
            type=type,
            use_container_width=use_container_width,
            page=ctx.active_script_hash if ctx else None,
        )

        # It doesn't make sense to create a button inside a form (except
        # for the "Form Submitter" button that's automatically created in
        # every form). We throw an error to warn the user about this.
        # We omit this check for scripts running outside streamlit, because
        # they will have no script_run_ctx.
        if runtime.exists():
            if is_in_form(self.dg) and not is_form_submitter:
                raise StreamlitAPIException(
                    f"`st.button()` can't be used in an `st.form()`.{FORM_DOCS_INFO}"
                )
            elif not is_in_form(self.dg) and is_form_submitter:
                raise StreamlitAPIException(
                    f"`st.form_submit_button()` must be used inside an `st.form()`.{FORM_DOCS_INFO}"
                )

        button_proto = ButtonProto()
        button_proto.id = id
        button_proto.label = label
        button_proto.default = False
        button_proto.is_form_submitter = is_form_submitter
        button_proto.form_id = current_form_id(self.dg)
        button_proto.type = type
        button_proto.use_container_width = use_container_width
        button_proto.disabled = disabled

        if help is not None:
            button_proto.help = dedent(help)

        serde = ButtonSerde()

        button_state = register_widget(
            "button",
            button_proto,
            user_key=key,
            on_change_handler=on_click,
            args=args,
            kwargs=kwargs,
            deserializer=serde.deserialize,
            serializer=serde.serialize,
            ctx=ctx,
        )

        if ctx:
            save_for_app_testing(ctx, id, button_state.value)
        self.dg._enqueue("button", button_proto)

        return button_state.value

    @property
    def dg(self) -> DeltaGenerator:
        """Get our DeltaGenerator."""
        return cast("DeltaGenerator", self)


def marshall_file(
    coordinates: str,
    data: DownloadButtonDataType,
    proto_download_button: DownloadButtonProto,
    mimetype: str | None,
    file_name: str | None = None,
) -> None:
    data_as_bytes: bytes
    if isinstance(data, str):
        data_as_bytes = data.encode()
        mimetype = mimetype or "text/plain"
    elif isinstance(data, io.TextIOWrapper):
        string_data = data.read()
        data_as_bytes = string_data.encode()
        mimetype = mimetype or "text/plain"
    # Assume bytes; try methods until we run out.
    elif isinstance(data, bytes):
        data_as_bytes = data
        mimetype = mimetype or "application/octet-stream"
    elif isinstance(data, io.BytesIO):
        data.seek(0)
        data_as_bytes = data.getvalue()
        mimetype = mimetype or "application/octet-stream"
    elif isinstance(data, io.BufferedReader):
        data.seek(0)
        data_as_bytes = data.read()
        mimetype = mimetype or "application/octet-stream"
    elif isinstance(data, io.RawIOBase):
        data.seek(0)
        data_as_bytes = data.read() or b""
        mimetype = mimetype or "application/octet-stream"
    else:
        raise RuntimeError("Invalid binary data format: %s" % type(data))

    if runtime.exists():
        file_url = runtime.get_instance().media_file_mgr.add(
            data_as_bytes,
            mimetype,
            coordinates,
            file_name=file_name,
            is_for_static_download=True,
        )
    else:
        # When running in "raw mode", we can't access the MediaFileManager.
        file_url = ""

    proto_download_button.url = file_url
