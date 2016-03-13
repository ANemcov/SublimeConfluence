import json
import os
import re
import sys

import requests
import sublime
import sublime_plugin

try:
    import lxml.html
    HTML_PRETTIFY = True
except ImportError:
    HTML_PRETTIFY = False

abspath = os.path.abspath(os.path.dirname(__file__))
sys.path.append(abspath)
import markdown2


class ConfluenceApi(object):

    def __init__(self, username, password, base_uri):
        self.username = username
        self.password = password
        self.base_uri = base_uri
        self.session = requests.Session()
        self.session.auth = requests.auth.HTTPBasicAuth(self.username, self.password)
        print("ConfluenceApi username: {}, password: {}, base_uri: {}".format(
            self.username, "*" * len(self.password), self.base_uri))

    def _request(self, method, sub_uri, params=None, **kwargs):
        url = "{}/{}".format(self.base_uri, sub_uri)
        headers = {"Content-Type": "application/json"}
        if params:
            kwargs.update(params=params)
        response = self.session.request(
            method, url, headers=headers, verify=False, **kwargs)
        return response

    def _post(self, url, data=None):
        return self._request("post", url, data=json.dumps(data))

    def _get(self, url, params=None):
        return self._request("get", url, params=params)

    def _put(self, url, data=None):
        return self._request("put", url, data=json.dumps(data))

    def _delete(self, url, params=None):
        return self._request("delete", url, params=params)

    def create_content(self, content_data):
        return self._post("content/", data=content_data)

    def search_content(self, space_key, title):
        cql = "type=page AND space={} AND title~{}".format(space_key, title)
        params = {"cql": cql}
        response = self._get("content/search", params=params)
        return response

    def get_content_by_id(self, content_id):
        response = self._get(
            "content/{}?expand=body.storage,version,space".format(content_id))
        return response

    def get_content_by_title(self, space_key, title):
        cql = "type=page AND space={} AND title={}".format(space_key, title)
        params = {"cql": cql}
        response = self._get("content/search", params=params)
        return response

    def get_content_history(self, content_id):
        return self._get("content/{}/history".format(content_id))

    def get_content_uri(self, content):
        base = content["_links"]["base"]
        webui = content["_links"]["webui"]
        return "{}{}".format(base, webui)

    def update_content(self, content_id, content_data):
        return self._put("content/{}".format(content_id),
                         data=content_data)

    def delete_content(self, content_id):
        return self._delete("content/{}".format(content_id))


class Markup(object):
    def __init__(self):
        self.markups = dict([
            ("Markdown", self.markdown_to_html),
            ("Markdown Extended", self.markdown_to_html),
            ("reStructuredText", self.rst_to_html)])

    def markdown_to_html(self, content):
        return markdown2.markdown(content).encode("utf-8").decode()

    def rst_to_html(self, content):
        try:
            from docutils.core import publish_string
            return publish_string(content, writer_name="html")
        except ImportError:
            error_msg = """
            RstPreview requires docutils to be installed for the python interpreter that Sublime uses.
            run: `sudo easy_install-2.6 docutils` and restart Sublime (if on Mac OS X or Linux).
            For Windows check the docs at https://github.com/d0ugal/RstPreview
            """
            sublime.error_message(error_msg)
            raise

    def to_html(self, content, syntax):
        syntax = syntax.split(".")[0].split("/")[-1]
        if syntax not in self.markups:
            sublime.error_message("Not support {} syntax yet".format(syntax))
            return
        else:
            converter = self.markups[syntax]
        new_content = converter(content)
        if not new_content:
            sublime.error_message(
                "Can not parse this document.")
        return new_content

    def get_meta_and_content(self, contents):
        meta = dict()
        content = list()
        tmp = contents.splitlines()
        for x, entry in enumerate(tmp):
            if entry.strip():
                if re.match(r"[Ss]pace: *", entry):
                    meta["space_key"] = re.sub("[^:]*: *", "", entry)
                elif re.match(r"[Aa]ncestor Title: *", entry):
                    meta["ancestor_title"] = re.sub("[^:]*: *", "", entry)
                elif re.match(r"[Tt]itle: *", entry):
                    meta["title"] = re.sub("[^:]*: *", "", entry)
            else:
                content = tmp[x + 1:]
                break
        return (meta, content)


class BaseConfluencePageCommand(sublime_plugin.TextCommand):
    """
    Base class for all Confluence commands. Handles getting an auth token.
    """
    MSG_USERNAME = "Confluence username:"
    MSG_PASSWORD = "Confluence password:"
    hidden_string = ""

    def run(self, edit):
        self.edit = edit
        settings = sublime.load_settings("Confluence.sublime-settings")
        self.base_uri = settings.get("base_uri")
        self.username = settings.get("username")
        self.password = settings.get("password") if settings.get("password") else ""
        self.default_space_key = settings.get("default_space_key")

    def build_confluence_api(self):
        if not self.username and not self.password:
            sublime.status_message("Waiting for username")
            sublime.set_timeout(self.get_username_password, 50)
        elif not self.username:
            sublime.status_message("Waiting for username")
            sublime.set_timeout(self.get_username, 50)
        elif not self.password:
            sublime.status_message("Waiting for password")
            sublime.set_timeout(self.get_password, 50)
        self.confluence_api = ConfluenceApi(self.username, self.password, self.base_uri)

    def get_username_password(self):
        self.view.window().show_input_panel(
            self.MSG_USERNAME, "", self.on_done_username_password, None, None)

    def get_username(self):
        self.view.window().show_input_panel(
            self.MSG_USERNAME, "", self.on_done_username, None, None)

    def get_password(self):
        self.view.window().show_input_panel(
            self.MSG_PASSWORD, "", self.on_done_password, self.on_change_password, None)

    def on_done_username_password(self, value):
        self.username = value
        sublime.status_message("Waiting for password")
        sublime.set_timeout(self.get_password, 50)

    def on_done_username(self, value):
        self.username = value

    def on_done_password(self, value):
        if not self.password.strip():
            sublime.status_message("No password provided")
        else:
            self.password = value

    def parse_input_password(self, input_password):
        length = len(input_password)
        for index, _ in enumerate(input_password, 1):
            if _ != "*":
                character = _
                position = index
                break
        else:
            character = "*"
            position = length
        return (length, character, position)

    def on_change_password(self, value):
        # Known issue
        # It can not get correct password when user modify the password inline
        if value != self.hidden_string:
            if len(value) < len(self.password):
                self.password = self.password[:len(value)]
            elif len(value) == len(self.password):
                (length, character, position) = self.parse_input_password(value)
                password = self.password[:length]
                self.password = password[:position - 1] + character + password[position:]
            else:
                self.password = self.password + value.replace("*", "")
            self.hidden_string = "*" * len(value)
            self.view.window().run_command("hide_panel", {"cancel": False})
            self.view.window().show_input_panel(
                self.MSG_PASSWORD, self.hidden_string, self.on_done_password,
                self.on_change_password, None)


class PostConfluencePageCommand(BaseConfluencePageCommand):
    def run(self, edit):
        super(PostConfluencePageCommand, self).run(edit)
        self.post()

    def post(self):
        region = sublime.Region(0, self.view.size())
        contents = self.view.substr(region)
        markup = Markup()
        meta, content = markup.get_meta_and_content(contents)
        syntax = self.view.settings().get("syntax")
        new_content = markup.to_html("\n".join(content), syntax)
        if not new_content:
            return
        self.build_confluence_api()
        response = self.confluence_api.get_content_by_title(
            meta["space_key"], meta["ancestor_title"])
        if response.ok:
            ancestor = response.json()["results"][0]
            ancestor_id = int(ancestor["id"])
            space = dict(key=meta["space_key"])
            new_content = "<p>This is a new page</p>"
            body = dict(storage=dict(value=new_content, representation="storage"))
            data = dict(type="page", title=meta["title"], ancestors=[dict(id=ancestor_id)],
                        space=space, body=body)
            self.confluence_api.create_content(data)
        else:
            print(response.text)
            sublime.error_message("Can not get ancestor, reason: {}".format(response.reason))


class GetConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SPACE_KEY = "Confluence space key:"
    MSG_SEARCH_PAGE = "Page title:"
    MSG_SUCCESS = "Content url copied to the clipboard."
    all_space = False
    specific_space_key = False

    def run(self, edit):
        super(GetConfluencePageCommand, self).run(edit)
        self.build_confluence_api()
        sublime.status_message("Waiting for page title")
        sublime.set_timeout(self.get_space_key_and_page_title, 50)

    def get_space_key_and_page_title(self):
        if self.all_space:
            self.space = None
            sublime.status_message("Waiting for page title")
            sublime.set_timeout(self.get_page_title, 50)
        elif self.specific_space_key:
            sublime.status_message("Waiting for space key")
            sublime.set_timeout(self.get_space_key, 50)
        elif not self.default_space_key:
            sublime.status_message("Waiting for space key")
            sublime.set_timeout(self.get_space_key, 50)
        else:
            self.space_key = self.default_space_key
            sublime.status_message("Waiting for page title")
            sublime.set_timeout(self.get_page_title, 50)

    def on_done_password(self, value):
        print("on_done_password")
        super(GetConfluencePageCommand, self).on_done_password(value)
        if not self.password.strip():
            sublime.status_message("No password provided")
        else:
            self.password = value
            sublime.set_timeout(self.get_space_key_and_page_title, 50)

    def get_space_key(self):
        self.view.window().show_input_panel(
            self.MSG_SPACE_KEY, "", self.on_done_space_key, None, None)

    def get_page_title(self):
        self.view.window().show_input_panel(
            self.MSG_SEARCH_PAGE, "", self.on_done_page_title, None, None)

    def on_done_space_key(self, value):
        self.space_key = value
        sublime.status_message("Waiting for page title")
        sublime.set_timeout(self.get_page_title, 50)

    def on_done_page_title(self, value):
        self.page_title = value
        sublime.set_timeout(self.get_pages, 50)

    def get_pages(self):
        response = self.confluence_api.search_content(self.space_key, self.page_title)
        if response.ok:
            self.pages = response.json()["results"]
            packed_pages = [page["title"] for page in self.pages]
            if packed_pages:
                self.view.window().show_quick_panel(packed_pages, self.on_done_pages)
            else:
                sublime.error_message("No result found for {}".format(self.page_title))
        else:
            print(response.text)
            sublime.error_message("Can not get pages, reason: {}".format(response.reason))

    def on_done_pages(self, idx):
        if idx == -1:
            return
        content_id = self.pages[idx]["id"]
        response = self.confluence_api.get_content_by_id(content_id)
        if response.ok:
            content = response.json()
            body = content["body"]["storage"]["value"]
            if HTML_PRETTIFY:
                document_root = lxml.html.fromstring(body)
                body = (lxml.etree.tostring(document_root, encoding="unicode", pretty_print=True))

            new_view = self.view.window().new_file()
            # set syntax file
            new_view.set_syntax_file("Packages/HTML/HTML.sublime-syntax")

            # insert the page
            new_view.run_command("insert_text", {"text": body})
            new_view.set_name(content["title"])
            new_view.settings().set("confluence_content", content)

            # copy content url
            content_uri = self.confluence_api.get_content_uri(content)
            sublime.set_clipboard(content_uri)
            sublime.status_message(self.MSG_SUCCESS)
        else:
            print(response.text)
            sublime.error_message("Can not get content, reason: {}".format(response.reason))


class UpdateConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SUCCESS = "Page updated and url copied to the clipboard."

    def run(self, edit):
        super(UpdateConfluencePageCommand, self).run(edit)
        self.content = self.view.settings().get("confluence_content")
        if not self.content:
            sublime.error_message(
                "Can't update: this doesn't appear to be a valid Confluence page.")
            return
        self.build_confluence_api()
        self.update()

    def update(self):
        # Example Data:
        """
        {
          "id": "3604482",
          "type": "page",
          "title": "new page",
          "space": {
            "key": "TST"
          },
          "body": {
            "storage": {
              "value": "<p>This is the updated text for the new page</p>",
              "representation": "storage"
            }
          },
          "version": {
            "number": 2
          }
        }
        """
        content_id = self.content["id"]
        title = self.content["title"]
        space_key = self.content["space"]["key"]
        version_number = self.content["version"]["number"] + 1
        body_value = self.view.substr(sublime.Region(0, self.view.size()))
        space = dict(key=space_key)
        version = dict(number=version_number, minorEdit=False)
        body = dict(storage=dict(value=body_value, representation="storage"))
        data = dict(id=content_id, type="page", title=title,
                    space=space, version=version, body=body)
        try:
            response = self.confluence_api.update_content(content_id, data)
            content_uri = self.confluence_api.get_content_uri(self.content)
            sublime.set_clipboard(content_uri)
            sublime.status_message(self.MSG_SUCCESS)
            if response.ok:
                self.view.settings().set("confluence_content", response.json())
        except Exception:
            print(response.text)
            sublime.error_message("Can not update content, reason: {}".format(response.reason))


class DeleteConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SUCCESS = "Confluence page has been deleted."

    def run(self, edit):
        super(DeleteConfluencePageCommand, self).run(edit)
        self.content = self.view.settings().get("confluence_content")
        if not self.content:
            sublime.error_message(
                "Can't update: this doesn't appear to be a valid Confluence page.")
            return
        self.build_confluence_api()
        self.delete()

    def delete(self):
        content_id = str(self.content["id"])
        try:
            response = self.confluence_api.delete_content(content_id)
            if response.ok:
                sublime.status_message(self.MSG_SUCCESS)
            else:
                print(response.text)
                sublime.error_message("Can't delete content, reason: {}".format(response.reason))
        except Exception:
            print(response.text)
            sublime.error_message("Can't delete content, reason: {}".format(response.reason))