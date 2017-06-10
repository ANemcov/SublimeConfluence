import json
import os
import re
import sys
import mimetypes

import requests
# from PIL import Image
import codecs

import sublime
import sublime_plugin

try:
    import lxml.html
    from lxml import etree
    HTML_PRETTIFY = True
except ImportError:
    HTML_PRETTIFY = False


abspath = os.path.abspath(os.path.dirname(__file__))
sys.path.append(abspath)
import markdown2


def debug_tab(sublime, content, header=""):
    new_view_source = sublime.view.window().new_file()
    # set syntax file
    new_view_source.set_syntax_file("Packages/HTML/HTML.sublime-syntax")
    new_view_source.settings().set("auto_indent", False)

    # insert the page
    new_view_source.run_command("insert", {"characters": "{}".format(content)})
    new_view_source.set_name("{}".format(header))
    new_view_source.run_command("expand_tabs", {"set_translate_tabs": True})


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
        if "headers" in kwargs:
            if not kwargs["headers"] is None:
                headers = kwargs["headers"]  # headers.update(kwargs["headers"])
            kwargs.pop("headers", None)

        if params:
            kwargs.update(params=params)
        # Ensure we are authenticated (set cookie, session, etc.)
        self.session.request("get", self.base_uri) 
        # Make the "real" call
        response = self.session.request(
            method, url, headers=headers, verify=False, **kwargs)
        return response

    def _post(self, url, data=None):
        return self._request("post", url, data=json.dumps(data))

    def _get(self, url, params=None):
        return self._request("get", url, params=params)

    def _put(self, url, data=None, files=None, headers=None):
        if data is None:
            return self._request("put", url, files=files, headers=headers)
        else:
            return self._request("put", url, data=json.dumps(data), files=files, headers=headers)

    def _delete(self, url, params=None):
        return self._request("delete", url, params=params)

    def extract_images(self, content_data, source_filename="/"):
        if HTML_PRETTIFY:
            doc = lxml.html.fromstring(content_data['body']['storage']['value'])

            file_dir = os.path.dirname(os.path.abspath(source_filename))
            print("Extract files form: %s \r\n" % file_dir)
            if not file_dir[-1] in "/\\":
                if sys.platform == "win32" or sys.platform == "win32":
                    file_dir += "\\"
                else:
                    file_dir += "/"

            resources = []

            for img in doc.xpath('//img'):
                img.tag = "ac:image"
                _src = (img.get('src')).replace("%20", " ")
                img.attrib.clear()
                # w, h = Image.open(file_dir + _src).size
                w = 500
                img.attrib["ac:width"] = "{}".format(min(w, 500))
                img.attrib["ac:align"] = "center"

                if os.path.isfile(file_dir + _src):
                    resources.append(dict({"filename": os.path.basename(file_dir + _src),
                                           "fullpath": file_dir + _src}))
                    _template = "<ri:attachment ri:filename=\"%s\" />" % os.path.basename(file_dir + _src)
                    link = lxml.html.fromstring(_template)  # .find('.//attachment')
                    link.tag = "ri:attachment"
                    img.insert(1, link)

            _tmp_cont = lxml.html.tostring(doc, pretty_print=True, encoding="utf-8").decode("utf-8")
            _tmp_cont =  _tmp_cont.replace("[TOC]", "<ac:structured-macro ac:name=\"toc\" ac:schema-version=\"1\" />")
            _tmp_cont = _tmp_cont.replace("atl_conf_", "ac:")
            _tmp_cont = _tmp_cont.replace("res_id_", "ri:")
            content_data['body']['storage']['value'] = _tmp_cont

            return content_data, resources
        else:
            return content_data, []

    def upload_child_attachment(self, content_id, attachment_dict):
        content_type, encoding = mimetypes.guess_type(attachment_dict["fullpath"])
        if content_type is None:
            content_type = 'multipart/form-data'
        return self._put("content/{}/child/attachment".format(content_id),
                         data=None,
                         files={"file": (attachment_dict["filename"],
                                         open(attachment_dict["fullpath"], 'rb'),
                                         content_type)},
                         headers={'X-Atlassian-Token': 'no-check'})

    def create_or_update_attachments(self, content_id, resources):

        for img in resources:
            upload_resp = self.upload_child_attachment(content_id, img)
            if not upload_resp.ok:
                return upload_resp

        return upload_resp

    def create_content(self, content_data, filename=None):

        new_content_data, images = self.extract_images(content_data, source_filename=filename)

        update_content_resp = self._post("content/", data=new_content_data)
        if not update_content_resp.ok:
            return update_content_resp, new_content_data

        content_id = self.get_content_id(update_content_resp.json())
        if images:
            upload_resp = self.create_or_update_attachments(content_id, images)
            if upload_resp.ok:
                return update_content_resp, new_content_data
            else:
                return upload_resp, new_content_data
        else:
            return update_content_resp, new_content_data

    def search_content(self, space_key, title):
        cql = "type=page AND space=\"{}\" AND title~\"{}\"".format(space_key, title)
        params = {"cql": cql}
        response = self._get("content/search", params=params)
        return response

    def get_content_by_id(self, content_id):
        response = self._get(
            "content/{}?expand=body.storage,version,space".format(content_id))
        return response

    def get_content_by_title(self, space_key, title):
        cql = "type=page AND space=\"{}\" AND title=\"{}\"".format(space_key, title)
        params = {"cql": cql}
        response = self._get("content/search", params=params)
        return response

    def get_content_history(self, content_id):
        return self._get("content/{}/history".format(content_id))

    def get_content_id(self, content):
        id = content["id"]
        return "{}".format(id)

    def get_content_uri(self, content):
        print("Page links: %s" % content["_links"])
        base = content["_links"]["base"]
        webui = content["_links"]["webui"]
        return "{}{}".format(base, webui)

    def update_content(self, content_id, content_data, filename=None):

        new_content_data, images = self.extract_images(content_data, source_filename=filename)

        update_content_resp = self._put("content/{}".format(content_id),
                                        data=new_content_data)
        if not update_content_resp.ok:
            return update_content_resp, new_content_data

        if images:
            upload_resp = self.create_or_update_attachments(content_id, images)
            if upload_resp.ok:
                return update_content_resp, new_content_data
            else:
                return upload_resp, new_content_data
        else:
            return update_content_resp, new_content_data

    def delete_content(self, content_id):
        return self._delete("content/{}".format(content_id))


class Markup(object):
    def __init__(self):
        self.markups = dict([
            ("Markdown", self.markdown_to_html),
            ("Markdown Extended", self.markdown_to_html),
            ("Markdown (Standard)", self.markdown_to_html),
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
    callback = None

    def run(self, edit):
        self.edit = edit
        settings = sublime.load_settings("Confluence.sublime-settings")
        self.base_uri = settings.get("base_uri")
        self.username = settings.get("username")
        self.password = settings.get("password") if settings.get("password") else ""
        self.default_space_key = settings.get("default_space_key")

    def get_credential(self):
        if not self.username and not self.password:
            sublime.status_message("Waiting for username")
            sublime.set_timeout(self.get_username_password, 50)
        elif not self.username:
            sublime.status_message("Waiting for username")
            sublime.set_timeout(self.get_username, 50)
        elif not self.password:
            sublime.status_message("Waiting for password")
            sublime.set_timeout(self.get_password, 50)
        else:
            callback = self.callback
            if callback:
                self.callback = None
                sublime.set_timeout(callback, 50)

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
        sublime.set_timeout(self.get_confluence_api, 50)

    def on_done_password(self, value):
        callback = self.callback
        if callback:
            self.callback = None
            sublime.set_timeout(callback, 50)

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
                (length, character, position) = self.parse_input_password(value)
                password = self.password
                self.password = password[:position - 1] + character + password[position - 1:]
            self.hidden_string = "*" * len(value)
            self.view.window().run_command("hide_panel", {"cancel": False})
            self.view.window().show_input_panel(
                self.MSG_PASSWORD, self.hidden_string, self.on_done_password,
                self.on_change_password, None)


class PostConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SUCCESS = "Content created and the url copied to the clipboard."

    def run(self, edit):
        super(PostConfluencePageCommand, self).run(edit)
        self.callback = self.post
        sublime.set_timeout(self.get_credential, 50)

    def post(self):
        region = sublime.Region(0, self.view.size())
        contents = self.view.substr(region)
        markup = Markup()
        meta, content = markup.get_meta_and_content(contents)
        syntax = self.view.settings().get("syntax")
        new_content = markup.to_html("\n".join(content), syntax)
        if not new_content:
            return
        self.confluence_api = ConfluenceApi(self.username, self.password, self.base_uri)
        response = self.confluence_api.get_content_by_title(
            meta["space_key"], meta["ancestor_title"])
        if response.ok:
            ancestor = response.json()["results"][0]
            ancestor_id = int(ancestor["id"])
            space = dict(key=meta["space_key"])
            body = dict(storage=dict(value=new_content, representation="storage"))
            data = dict(type="page", title=meta["title"], ancestors=[dict(id=ancestor_id)],
                        space=space, body=body)
            result, mod_content = self.confluence_api.create_content(data, self.view.file_name())
            if result.ok:
                self.view.settings().set("confluence_content", result.json())
                # copy content url
                content_uri = self.confluence_api.get_content_uri(result.json())
                sublime.set_clipboard(content_uri)
                sublime.status_message(self.MSG_SUCCESS)
            else:
                print(result.text)

                debug_tab(self, new_content, "Source")
                debug_tab(self, mod_content, "Modified")

                sublime.error_message("Can not create content, reason: {}".format(result.reason))
        else:
            print(response.text)

            debug_tab(self, new_content, "Source")

            sublime.error_message("Can not get ancestor, reason: {}".format(response.reason))


class GetConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SPACE_KEY = "Confluence space key:"
    MSG_SEARCH_PAGE = "Page title:"
    MSG_SUCCESS = "Content url copied to the clipboard."
    all_space = False
    specific_space_key = False

    def run(self, edit):
        super(GetConfluencePageCommand, self).run(edit)
        self.callback = self.get_space_key_and_page_title
        sublime.set_timeout(self.get_credential, 50)

    def get_space_key_and_page_title(self):
        if self.all_space:
            self.space = None
            sublime.set_timeout(self.get_page_title, 50)
        elif self.specific_space_key:
            sublime.set_timeout(self.get_space_key, 50)
        elif not self.default_space_key:
            sublime.set_timeout(self.get_space_key, 50)
        else:
            self.space_key = self.default_space_key
            sublime.set_timeout(self.get_page_title, 50)

    def get_space_key(self):
        sublime.status_message("Waiting for space key")
        self.view.window().show_input_panel(
            self.MSG_SPACE_KEY, "", self.on_done_space_key, None, None)

    def get_page_title(self):
        sublime.status_message("Waiting for page title")
        self.view.window().show_input_panel(
            self.MSG_SEARCH_PAGE, "", self.on_done_page_title, None, None)

    def on_done_space_key(self, value):
        self.space_key = value
        sublime.set_timeout(self.get_page_title, 50)

    def on_done_page_title(self, value):
        self.page_title = value
        sublime.set_timeout(self.get_pages, 50)

    def get_pages(self):
        self.confluence_api = ConfluenceApi(self.username, self.password, self.base_uri)
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
            new_view.settings().set("auto_indent", False)

            # insert the page
            new_view.run_command("insert", {"characters": body})
            new_view.set_name(content["title"])
            new_view.settings().set("confluence_content", content)
            new_view.settings().set("auto_indent", True)
            new_view.run_command("reindent", {"single_line": False})
            new_view.run_command("expand_tabs", {"set_translate_tabs": True})

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
        if self.content and self.content.get('id'):
            self.callback = self.update_from_editor
        else:
            self.callback = self.update_from_source
        sublime.set_timeout(self.get_credential, 50)

    def update_from_editor(self):
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
        region = sublime.Region(0, self.view.size())
        contents = self.view.substr(region)
        syntax = self.view.settings().get("syntax")
        if "HTML" in syntax:
            new_content = "".join(contents.split("\n"))
        else:
            markup = Markup()
            meta, content = markup.get_meta_and_content(contents)
            new_content = markup.to_html("\n".join(content), syntax)
        space = dict(key=space_key)
        version = dict(number=version_number, minorEdit=False)
        body = dict(storage=dict(value=new_content, representation="storage"))
        data = dict(id=content_id, type="page", title=title,
                    space=space, version=version, body=body)
        try:
            self.confluence_api = ConfluenceApi(self.username, self.password, self.base_uri)
            response, mod_content = self.confluence_api.update_content(content_id,
                                                                       data,
                                                                       self.view.file_name())

            if response.ok:
                content_uri = self.confluence_api.get_content_uri(self.content)
                sublime.set_clipboard(content_uri)

                debug_tab(self, mod_content, "Modified")

                sublime.status_message(self.MSG_SUCCESS)
                self.view.settings().set("confluence_content", response.json())
            else:
                print(response.text)

                debug_tab(self, mod_content, "Modified")

                sublime.error_message("Can't update content, reason: {}".format(response.reason))
        except Exception:
            print(response.text)

            debug_tab(self, mod_content, "Modified")

            sublime.error_message("Can't update content, reason: {}".format(response.reason))

    def update_from_source(self):

        current_filename = self.view.file_name()
        region = sublime.Region(0, self.view.size())
        contents = self.view.substr(region)
        markup = Markup()
        meta, content = markup.get_meta_and_content(contents)
        syntax = self.view.settings().get("syntax")
        new_content = markup.to_html("\n".join(content), syntax)
        if not new_content:
            sublime.error_message(
                "Can't update: this doesn't appear to be a valid Confluence page.")
            return
        self.confluence_api = ConfluenceApi(self.username, self.password, self.base_uri)

        get_content_by_title_resp = self.confluence_api.get_content_by_title(
            meta["space_key"], meta["title"])
        if get_content_by_title_resp.ok:
            content_id = get_content_by_title_resp.json()["results"][0]["id"]

            get_content_by_id_resp = self.confluence_api.get_content_by_id(content_id)
            if get_content_by_id_resp.ok:
                content = get_content_by_id_resp.json()
                space = dict(key=meta["space_key"])
                version_number = content["version"]["number"] + 1
                version = dict(number=version_number, minorEdit=False)
                # ancestor_id = int(ancestor["id"])
                body = dict(storage=dict(value=new_content, representation="storage"))
                data = dict(id=content_id, type="page", title=meta["title"],
                            space=space, version=version, body=body)

                update_content_resp, mod_content = self.confluence_api.update_content(content_id,
                                                                                      data,
                                                                                      current_filename)

                if update_content_resp.ok:
                    self.view.settings().set("confluence_content", update_content_resp.json())
                    content_uri = self.confluence_api.get_content_uri(update_content_resp.json())

                    sublime.set_clipboard(content_uri)
                    sublime.status_message(self.MSG_SUCCESS)
                else:
                    print(update_content_resp.text)

                    debug_tab(self, new_content, "Source")
                    debug_tab(self, mod_content, "Modified")

                    sublime.error_message("Can not update content, reason: {}".format(
                        update_content_resp.reason))
            else:
                print(get_content_by_id_resp.text)

                debug_tab(self, new_content, "Source")

                sublime.error_message("Can not get content by id, reason: {}".format(
                    get_content_by_id_resp.reason))
        else:
            print(get_content_by_title_resp.text)

            sublime.error_message("Can not get content by title, reason: {}".format(
                get_content_by_title_resp.reason))


class DeleteConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SUCCESS = "Confluence page has been deleted."

    def run(self, edit):
        super(DeleteConfluencePageCommand, self).run(edit)
        self.content = self.view.settings().get("confluence_content")
        if not self.content:
            sublime.error_message(
                "Can't update: this doesn't appear to be a valid Confluence page.")
            return
        self.callback = self.delete
        sublime.set_timeout(self.get_credential, 50)

    def delete(self):
        content_id = str(self.content["id"])
        try:
            self.confluence_api = ConfluenceApi(self.username, self.password, self.base_uri)
            response = self.confluence_api.delete_content(content_id)
            if response.ok:
                sublime.status_message(self.MSG_SUCCESS)
            else:
                print(response.text)
                sublime.error_message("Can't delete content, reason: {}".format(response.reason))
        except Exception:
            print(response.text)
            sublime.error_message("Can't delete content, reason: {}".format(response.reason))
