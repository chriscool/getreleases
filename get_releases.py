#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Get various Git-related releases for Git Rev News.

This needs BeautifulSoup 4 and an xml parser. Both can be installed
with:

    $ pip install bs4
    $ pip install lxml

This works by parsing a release page or a tag page for each
supported software.

Currently supports : Git, Git for Windows, libgit2, libgit2sharp,
GitHub Enterprise, GitLab, Bitbucket, GitKraken, GitHub Desktop,
tig, Sourcetree, git-credential-azure, git-credential-oauth.

To get help about all the supported options, this should be used
like this:

  ./get_releases.py --help

To list all supported software and what URL is used, this should
be used like this:

  ./get_releases.py --list

To actually generate release information, this should be used
like this:

  ./get_releases.py --user XXXXXXXX --password YYYYYYYY

where XXXXXXXX is the GitHub username and YYYYYYYY is a personal
access token created from:

  https://github.com/settings/tokens

The token needs only the repo/public_repo (Access public repositories)
scope.

For more information, see:

  https://developer.github.com/changes/2013-09-03-two-factor-authentication/
"""

import argparse
import datetime
import re
from urllib.parse import urljoin, quote_plus
import requests

from bs4 import BeautifulSoup

PARSER = argparse.ArgumentParser()
PARSER.add_argument('-s', '--since', help='Get releases since that date. Format: YYYY-MM-DD. Default is 30 days before today.')
PARSER.add_argument('-u', '--user', help='GitHub API user (required for GitHub repos).')
PARSER.add_argument('-p', '--password', help='GitHub API password (required for GitHub repos).')
PARSER.add_argument('-l', '--list', help='List supported software and their URL.', action="store_true")
PARSER.add_argument('-g', '--get', help='Get releases only for software matching that string.')
PARSER.add_argument('-e', '--exact', help='Perform an exact, case-sensitive match when using --get.', action='store_true')
PARSER.add_argument('-d', '--debug', help='Show debugging information.', action="store_true")

ARGS = PARSER.parse_args()

if ARGS.since:
    DATE = datetime.datetime.strptime(ARGS.since, '%Y-%m-%d').date()
else:
    DATE = datetime.date.today() - datetime.timedelta(days=30)

def get_date(string, fmt):
    string = re.sub(r'(\d)(th|st|nd|rd)', '\\1', string)

    # Use word boundary \b to replace 'Sept' only when it's a whole word
    # (useful for SourceTree)
    string = re.sub(r'\bSept\b', 'Sep', string)

    formats = fmt if isinstance(fmt, list) else [fmt]

    for format_str in formats:
        try:
            # Try to parse the date
            return datetime.datetime.strptime(string, format_str).date()
        except ValueError as e:
            continue

    # If all formats failed, print a warning and return None
    print(f"Warning: Could not parse date string '{string}' with any of the formats: {formats}")
    return None

def format_title(title):
    return '+ {} '.format(title)

class Releases():

    def __init__(self, url):
        self._last_date = DATE
        self._debug = ARGS.debug
        self._releases = dict()
        self._url = url
        self._replace_url = False

    def markdown(self, title):
        if not self._releases:
            print('No release for {}!'.format(title))
            return ''

        return format_title(title) + self._format_items() + '\n'

    def _format_items(self, start_index=0):
        fmt = '[{}]({})'
        result = ''

        for i, (version, href) in enumerate(self._releases.items(), start=start_index):
            if i > 0:
                result += ',\n'

            href = self._url if self._replace_url else urljoin(self._url, href)
            result += fmt.format(version, href)

        return result

    def _print_debug(self, string):
        if self._debug:
            print('--->')
            print(string)
            print('<---')

class HtmlPage(Releases):

    def __init__(self, url, pattern=r'(\d+\.\d+\.?\d*)', parser='html.parser', user_agent=None):
        Releases.__init__(self, url)
        self._pattern = pattern
        self._parser = parser
        self._user_agent = user_agent

    def get_releases(self):
        self._soup = self._get_soup()
        self._pattern = re.compile(self._pattern, re.IGNORECASE)

    def _get_soup(self):
        print('> Requesting {}'.format(self._url))
        headers = None
        if self._user_agent:
            headers = self._user_agent
        request = requests.get(self._url, headers=headers)

        if request.ok:
            soup = BeautifulSoup(request.text, self._parser)
        else:
            print('Error {}'.format(request.status_code))
            soup = None

        return soup


class HtmlNestedPage(HtmlPage):

    def __init__(self, url, pattern=r'(\d+\.\d+\.?\d*)', parser='html.parser',
                 parent=None, date=None, releases=None, user_agent=None):
        HtmlPage.__init__(self, url, pattern, parser, user_agent)

        self._parent = parent
        self._date = date
        self._rel = releases

    def get_releases(self):
        HtmlPage.get_releases(self)

        if not self._soup:
            return

        if self._parent:
            self._get_releases_in_parent()
        else:
            self._extract_releases(self._soup)

    def _get_releases_in_parent(self):
        parents = self._soup.find_all(*self._parent)
        self._print_debug('Getting releases from parents:')
        self._print_debug(parents)

        for parent in parents:
            self._print_debug('Getting releases from parent: {}'.format(parent))
            if self._date:
                dates = parent.find_all(*self._date['elt'])
                self._print_debug('Getting releases from dates ({}):'.format(*self._date['elt']))
                self._print_debug(dates)

                for date in dates:
                    if 'link' in self._date:
                        string = date.get('href')
                    else:
                        string = date.text.strip()

                    date = self._extract_date_from_string(string)

                    if not date or date < self._last_date:
                        continue

                    if 'link' in self._date:
                        self._extract_releases(parent, string)
                    else:
                        self._extract_releases(parent)

    def _extract_date_from_string(self, string):
        self._print_debug('Date in string: {}'.format(string))

        if 'pattern' in self._date:
            self._print_debug('Searching pattern: {}'.format(self._date['pattern']))
            match = re.search(self._date['pattern'], string)
            if not match:
               print(f"Warning: Date pattern '{self._date['pattern']}' not found in string '{string}'")
               return None
            string = match.group(1)
            self._print_debug('String after pattern: {}'.format(string))

        date = get_date(string, self._date['fmt'])
        if date:
            self._print_debug(f"Date found: {date}")

        return date

    def _extract_releases(self, element, href=None):
        if self._rel:
            self._extract_releases_with_elts(element, href)
        else:
            self._extract_releases_from_links(element)

    def _extract_releases_with_elts(self, element, href):
        rel = element.find(*self._rel['number'])
        self._print_debug('Getting releases from elements ({}) ({}):'.format(element, *self._rel['number']))
        self._print_debug(rel)

        if not rel:
            return

        self._print_debug('Getting relnum from ({}) ({}):'.format(self._pattern, rel.text))
        versions = re.findall(self._pattern, rel.text)
        if not versions:
            return
        relnum = ', '.join(versions)
        self._print_debug(relnum)

        if 'link' in self._rel:
            relhref = element.find(*self._rel['link'])
            if 'a' not in self._rel['link']:
                relhref = relhref.find('a')
            href = relhref.get('href')

        if relnum:
            self._releases.update({relnum: href})

    def _extract_releases_from_links(self, element):
        links = element.find_all('a')
        self._print_debug('Getting releases from links:')
        self._print_debug(links)

        if not links:
            return

        for link in links:
            link_match = re.search(self._pattern, link.text)

            if link_match:
                self._releases.update({link_match.group(1): link.get('href')})

class HtmlFlatPage(HtmlPage):

    def __init__(self, url, pattern=r'(\d+\.\d+\.?\d*)', parser='html.parser',
                  releases=None, date=False, custom_url='', user_agent=None):
        HtmlPage.__init__(self, url, pattern, parser, user_agent)

        self._date = date
        self._rel = releases
        self._custom_url = custom_url

    def _explore_next_nodes(self, start_node):
        next_node = start_node

        while True:
            next_node = next_node.nextSibling

            if not next_node or getattr(next_node, 'name', None) == self._rel['number'][0]:
                return None

            if getattr(next_node, 'name', None) == self._date['elt'][0]:
                datematch = re.search(self._date['pattern'], next_node.text)

                if datematch:
                    return get_date(datematch.group(1), self._date['fmt'])

    def _get_custom_url(self, relnum):
        if not self._custom_url:
            return self._url

        rel_elems = re.findall(r'\d+', relnum)
        return self._custom_url.format(*rel_elems)

    def get_releases(self):
        HtmlPage.get_releases(self)

        self._print_debug('Getting release numbers from {}'.format(*self._rel['number']))

        nodes = self._soup.find_all(*self._rel['number'])

        for node in nodes:
            relnum = re.search(self._pattern, node.text)
            date = self._explore_next_nodes(node)

            if date and date < self._last_date:
                continue

            if relnum:
                self._releases.update({relnum.group(1): self._get_custom_url(relnum.group(1))})

class GitHubTags(Releases):

    def __init__(self, repo, regex, url='', replace_url=False):
        Releases.__init__(self, url)

        self._api_user = ARGS.user
        self._api_pass = ARGS.password

        self._api_url = 'https://api.github.com/repos/' + repo + '/tags'
        self._tag_url = 'https://github.com/' + repo + '/releases/tag/'
        self._repo = repo
        self._regex = re.compile(regex)

        self._replace_url = replace_url

    def get_releases(self):
        print('> Getting releases from GitHub repo: {}'.format(self._repo))
        request = requests.get(self._api_url, auth=(self._api_user, self._api_pass))

        if not request.ok:
            print('Error {} while querying GitHub API'.format(request.status_code))
        else:
            json = request.json()

            for tag in json:
                self._print_debug('tag: {}'.format(tag['name']))
                tag_name = re.match(self._regex, tag['name'])
                self._print_debug('tag_name: {}'.format(tag_name))
                if not tag_name:
                    continue

                if self._last_date:
                    self._print_debug('Getting commit url: {}'.format(tag['commit']['url']))
                    sha_req = requests.get(tag['commit']['url'],
                                           auth=(self._api_user, self._api_pass))

                    if sha_req.ok:
                        sha = sha_req.json()
                        commit_date = sha['commit']['committer']['date']
                        commit_date = get_date(commit_date[0:10], '%Y-%m-%d')

                        if commit_date < self._last_date:
                            break

                try:
                    version = tag_name.group(1) + '(' + tag_name.group(2) + ')'
                except IndexError:
                    version = tag_name.group(1)

                self._releases.update({version: self._tag_url + tag['name']})

class MultiReleases(Releases):

    def __init__(self, multi_releases):
        Releases.__init__(self, '')

        self._multi_releases = multi_releases

    def get_releases(self):
        for releases in self._multi_releases:
            releases.get_releases()

    def markdown(self, title):
        total_items = 0
        result = format_title(title)
        for releases in self._multi_releases:
            if releases._releases:
                result += releases._format_items(start_index=total_items)
                total_items += len(releases._releases)
        if total_items == 0:
            print('No release for {}!'.format(title))
            return ''
        return result + '\n'

class GitLabTags(Releases):
    """
    Get releases from a GitLab project's tags via the REST API.

    Mirrors the interface of GitHubTags. For public projects, no token is
    required. The first capture group in `regex` is the version; if a second
    capture group is present, it is appended in parentheses (matching
    GitHubTags' two-group convention).
    """

    def __init__(self, project, regex,
                 base_url='https://gitlab.com',
                 url='', replace_url=False, token=None):
        Releases.__init__(self, url)
        self._project = project
        self._regex = re.compile(regex)
        self._base_url = base_url.rstrip('/')
        self._replace_url = replace_url
        self._token = token

        encoded = quote_plus(project)
        self._api_url = '{}/api/v4/projects/{}/repository/tags'.format(self._base_url, encoded)
        self._tag_url = '{}/{}/-/tags/'.format(self._base_url, project)

    def _headers(self):
        headers = {'User-Agent': 'Mozilla/5.0'}
        if self._token:
            headers['PRIVATE-TOKEN'] = self._token
        return headers

    def _iter_tags(self, max_pages=5, per_page=100):
        params = {'per_page': per_page, 'order_by': 'updated', 'sort': 'desc'}
        for _ in range(max_pages):
            try:
                resp = requests.get(self._api_url, params=params,
                                    headers=self._headers(), timeout=30)
            except requests.exceptions.RequestException as e:
                print('Error querying GitLab API: {}'.format(e))
                return
            if not resp.ok:
                print('Error {} querying GitLab API for {}'.format(
                    resp.status_code, self._project))
                return
            for tag in resp.json():
                yield tag
            next_page = resp.headers.get('X-Next-Page')
            if not next_page:
                return
            params = {'per_page': per_page, 'page': next_page,
                      'order_by': 'updated', 'sort': 'desc'}

    def date_map(self, since=None):
        """Return {version: date} from matching tags. Stops at the first tag
        older than `since` (since tags are returned newest-first)."""
        result = {}
        for tag in self._iter_tags():
            m = self._regex.match(tag['name'])
            if not m:
                continue
            committed = (tag.get('commit') or {}).get('committed_date', '')
            if not committed:
                continue
            date = get_date(committed[:10], '%Y-%m-%d')
            if not date:
                continue
            if since and date < since:
                break
            result[m.group(1)] = date
        return result

    def get_releases(self):
        print('> Getting releases from GitLab project: {}'.format(self._project))
        for tag in self._iter_tags():
            self._print_debug('tag: {}'.format(tag['name']))
            m = self._regex.match(tag['name'])
            self._print_debug('tag_name: {}'.format(m))
            if not m:
                continue
            committed = (tag.get('commit') or {}).get('committed_date', '')
            if committed and self._last_date:
                date = get_date(committed[:10], '%Y-%m-%d')
                if date and date < self._last_date:
                    break
            try:
                version = m.group(1) + '(' + m.group(2) + ')'
            except IndexError:
                version = m.group(1)
            self._releases[version] = self._tag_url + tag['name']


class GitLabReleases(Releases):
    BASE_URL = 'https://docs.gitlab.com'
    INDEX_URL = BASE_URL + '/releases/'
    DATE_RE = re.compile(r'On ([A-Z][a-z]+ \d{1,2}, \d{4}),')
    TAGS_PROJECT = 'gitlab-org/gitlab'
    TAGS_REGEX = r'^v(\d+\.\d+\.\d+)(?:-ee)?$'
    TAG_FUDGE_DAYS = 7
    MAJOR_HREF_RE = re.compile(r'^/releases/(\d+)/$')
    PATCHES_HREF = '/releases/patches/'

    def __init__(self, num_majors=2, include_patches=True,
                 confirm_with_tags=True, tags_project=None, tags_token=None):
        Releases.__init__(self, self.INDEX_URL)
        self._num_majors = num_majors
        self._include_patches = include_patches
        self._confirm_with_tags = confirm_with_tags
        self._tags_project = tags_project or self.TAGS_PROJECT
        self._tags_token = tags_token
        self._tag_dates = {}
        self._debug = ARGS.debug
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': 'Mozilla/5.0'})

    @staticmethod
    def _canonicalize(version):
        if re.match(r'^\d+\.\d+$', version):
            return version + '.0'
        if re.match(r'^\d+\.\d+\.\d+$', version):
            return version
        return None

    def _load_tag_dates(self):
        if not self._confirm_with_tags:
            return
        since = self._last_date - datetime.timedelta(days=self.TAG_FUDGE_DAYS)
        tags = GitLabTags(self._tags_project, self.TAGS_REGEX,
                          token=self._tags_token)
        print('> Fetching tag dates from GitLab project: {}'.format(self._tags_project))
        try:
            self._tag_dates = tags.date_map(since=since)
        except Exception as e:
            print('Warning: failed to fetch tag dates: {}'.format(e))
            self._tag_dates = {}
        self._print_debug('Tag dates loaded: {} entries'.format(len(self._tag_dates)))

    def _fetch_soup(self, url):
        self._print_debug('Requesting {}'.format(url))
        try:
            response = self._session.get(url, timeout=30)
        except requests.exceptions.RequestException as e:
            print('Error fetching {}: {}'.format(url, e))
            return None

        if not response.ok:
            print('Error {} fetching {}'.format(response.status_code, url))
            return None

        return BeautifulSoup(response.text, 'html.parser')

    def _section_urls(self):
        print('> Requesting {}'.format(self.INDEX_URL))
        soup = self._fetch_soup(self.INDEX_URL)
        if not soup:
            return []

        majors = []
        patches_url = None

        for card in soup.find_all('a', class_='card-body'):
            href = card.get('href', '')
            if not href:
                continue
            major_match = self.MAJOR_HREF_RE.match(href)
            if major_match:
                majors.append((int(major_match.group(1)), urljoin(self.BASE_URL, href)))
            elif href == self.PATCHES_HREF and self._include_patches:
                patches_url = urljoin(self.BASE_URL, href)

        majors.sort(key=lambda item: item[0], reverse=True)
        urls = [url for _, url in majors[:self._num_majors]]
        if patches_url:
            urls.append(patches_url)

        self._print_debug('Section URLs: {}'.format(urls))
        return urls

    def _section_releases(self, section_url):
        print('> Requesting {}'.format(section_url))
        soup = self._fetch_soup(section_url)
        if not soup:
            return []

        results = []
        for card in soup.find_all('a', class_='card-body'):
            href = card.get('href', '')
            if not href or href.endswith('/older/'):
                continue

            title_div = card.find('div', class_='card-title')
            if not title_div:
                continue

            version = title_div.get('id') or ''
            if not re.match(r'^\d+(?:\.\d+){1,2}$', version):
                title_text = title_div.get_text(strip=True).replace('\xa0', ' ')
                title_text = re.sub(r'^GitLab\s+', '', title_text)
                version = title_text

            if not version:
                continue

            results.append((version, urljoin(self.BASE_URL, href)))

        return results

    def _release_date(self, release_url):
        soup = self._fetch_soup(release_url)
        if not soup:
            return None

        text = soup.get_text(' ', strip=True)
        match = self.DATE_RE.search(text)
        if not match:
            return None

        return get_date(match.group(1), '%B %d, %Y')

    def _date_from_tags(self, version):
        primary = version.split(',')[0].strip()
        canonical = self._canonicalize(primary)
        if canonical and canonical in self._tag_dates:
            return self._tag_dates[canonical]
        return None

    def get_releases(self):
        self._load_tag_dates()
        for section_url in self._section_urls():
            for version, release_url in self._section_releases(section_url):
                date = self._date_from_tags(version)
                if date is not None:
                    self._print_debug(
                        'Tag date for {}: {}'.format(version, date))
                else:
                    date = self._release_date(release_url)
                if date is None:
                    self._print_debug(
                        'No parseable date for {}, including it (assumed recent).'.format(release_url))
                    self._releases[version] = release_url
                    continue
                if date < self._last_date:
                    self._print_debug(
                        'Stopping section {} at {} ({} < {})'.format(
                            section_url, version, date, self._last_date))
                    break
                self._releases[version] = release_url


class GitHubReleases(Releases):
    """
    Get releases from the GitHub Releases API.
    """
    def __init__(self, repo, include_prereleases=False, pattern=None, version_format=None):
        Releases.__init__(self, f"https://github.com/{repo}/releases")
        self._api_url = f"https://api.github.com/repos/{repo}/releases"
        self._api_user = ARGS.user
        self._api_pass = ARGS.password
        self._repo = repo
        self._include_prereleases = include_prereleases
        self._pattern = re.compile(pattern) if pattern else None
        self._version_format = version_format

    def get_releases(self):
        print(f"> Getting releases from GitHub API: {self._repo}")
        try:
            request = requests.get(self._api_url, auth=(self._api_user, self._api_pass))
            request.raise_for_status()
            releases = request.json()
        except requests.exceptions.RequestException as e:
            print(f"Error querying GitHub API: {e}")
            return

        for release in releases:
            if release.get('prerelease') and not self._include_prereleases:
                continue

            date_str = release.get('published_at', '').split('T')[0]
            if not date_str:
                continue

            release_date = get_date(date_str, '%Y-%m-%d')
            if not release_date or release_date < self._last_date:
                continue

            tag_name = release.get('tag_name')
            url = release.get('html_url')
            if tag_name and url:
                display_version = tag_name
                # If a pattern and format are provided, try to reformat the tag
                if self._pattern and self._version_format:
                    match = self._pattern.match(tag_name)
                    if match:
                        display_version = self._version_format.format(*match.groups())

                self._releases[display_version] = url

RELEASES = {
    'Git': HtmlNestedPage('https://lore.kernel.org/git/?q=d%3A{:%Y%m%d}..+%5BANNOUNCE%5D+Git'.format(DATE),
                          pattern=r'^\[ANNOUNCE\] Git v?(\d\.\d+.*)',
                          user_agent={'User-Agent': 'Wget/1.21.3 (compatible)'}),
    'Git for Windows': GitHubReleases('git-for-windows/git',
                                      include_prereleases=True,
                                      pattern=r'v(\d+\.\d+\.\d+(?:-rc\d+)?)\.windows\.(\d+)',
                                      version_format='v{0}({1})'),
    'libgit2': GitHubTags('libgit2/libgit2', r'^v(\d\.\d+\.\d+)$'),
    'libgit2sharp': GitHubTags('libgit2/libgit2sharp', r'^v(\d\.\d+\.?\d*)$'),
    'go-git': GitHubTags('go-git/go-git', r'^v(\d+\.\d+\.\d+(?:-[a-z]+\.?\d*)?)$'),
    'gitoxide': GitHubTags('GitoxideLabs/gitoxide', r'^v(\d+\.\d+\.\d+)$'),
    'JGit': GitHubTags('eclipse-jgit/jgit', r'^v(\d+\.\d+\.\d+)\.\d+-r$'),
    'GitHub Enterprise': HtmlNestedPage('https://enterprise.github.com/releases/',
                                        parent=['h3'],
                                        date={'elt': ['small', {'class': 'release-date'}], 'fmt': '%B %d, %Y'}),
    'GitLab': GitLabReleases(num_majors=2, include_patches=True),
    'Gitea': GitHubTags('go-gitea/gitea', r'^v(\d+\.\d+\.\d+)$'),
    'Bitbucket Data Center': HtmlFlatPage('https://confluence.atlassian.com/bitbucketserver/release-notes-872139866.html',
                                          pattern=r'(\d+\.\d+)',
                                          releases={'number': ['h2']},
                                          date={'elt': ['p', 'strong'],
                                                'pattern': '(.*)',
                                                'fmt': '%d %B %Y'}),
    'Gerrit Code Review': HtmlFlatPage('https://gerrit-releases.storage.googleapis.com/',
                                       pattern=r'gerrit-(\d+\.\d+.*)\.war',
                                       parser='xml',
                                       releases={'number': ['Key']},
                                       date={'elt': ['LastModified'],
                                             'pattern': r'(.*)T.*',
                                             'fmt': '%Y-%m-%d'},
                                       custom_url='https://www.gerritcodereview.com/{0}.{1}.html#{0}{1}{2}'),
    'GitKraken': HtmlFlatPage('https://help.gitkraken.com/gitkraken-desktop/current/',
                              releases={'number': ['h2']},
                              date={'elt': ['p'],
                                    'pattern': r'([A-Za-z]+, [A-Za-z]+ \d+.., \d{4})',
                                    'fmt': '%A, %B %d, %Y'},
                              user_agent={'User-Agent': 'Mozilla/5.0'}),
    'GitHub Desktop': GitHubTags('desktop/desktop', r'^release-(\d\.\d\.\d+)$',
                                 url='https://desktop.github.com/release-notes/', replace_url=True),
    'Sourcetree': HtmlNestedPage('https://www.sourcetreeapp.com/download-archives',
                                 pattern=r'(\d\.\d\.?\d*\.?\d*)',
                                 parent=['tr'],
                                 releases={'number': ['div'], 'link': ['small']},
                                 date={'elt': ['td', {'data-label': 'Build Date'}],
                                       'fmt': ['%d-%b-%Y', '%d-%B-%Y']}),
    'tig': GitHubTags('jonas/tig', r'^tig-(\d\.\d+\.\d+)$'),
    'lazygit': GitHubTags('jesseduffield/lazygit', r'^v(\d+\.\d+\.\d+)$'),
    'gitui': GitHubTags('gitui-org/gitui', r'^v(\d+\.\d+\.\d+)$'),
    'Garden': GitHubTags('garden-rs/garden', r'^v(\d\.\d+\.\d+)$'),
    'Git Cola': GitHubTags('git-cola/git-cola', r'^v(\d\.\d+\.\d+)$'),
    'GitButler': GitHubTags('gitbutlerapp/gitbutler', r'^release/(\d\.\d+\.\d+)$'),
    'Sublime Merge': HtmlNestedPage('https://www.sublimemerge.com/download',
                                    parent=['article'],
                                    pattern=r'(Build\s+\d+)',
                                    releases={'number': ['h3']},
                                    date={'elt': ['div', {'class': 'release-date'}],
                                          'fmt': ['%d %B %Y', '%d %b %Y']}),
    'delta': GitHubTags('dandavison/delta', r'^(\d+\.\d+\.\d+)$'),
    'difftastic': GitHubTags('Wilfred/difftastic', r'^(\d+\.\d+\.\d+)$'),
    'Kinetic Merge': GitHubTags('sageserpent-open/kineticMerge', r'^v(\d\.\d+\.\d+)$'),
    'git-credential-azure': GitHubTags('hickford/git-credential-azure', r'^v(\d\.\d+\.\d+)$'),
    'git-credential-oauth': GitHubTags('hickford/git-credential-oauth', r'^v(\d\.\d+\.\d+)$'),
    'git-lfs': GitHubTags('git-lfs/git-lfs', r'^v(\d+\.\d+\.\d+)$'),
    'b4': GitHubTags('mricon/b4', r'^v(\d+\.\d+\.\d+)$'),
}

if ARGS.list:
    print("Supported software:")
    for name, releases in RELEASES.items():
        print('\t{} <-- {}'.format(name, releases._url))
    exit(0)

if ARGS.get:
    if ARGS.exact:
        print('Getting releases for exactly "{}" since {}\n'.format(ARGS.get, DATE))
    else:
        print('Getting releases only for software matching "{}" since {}\n'.format(ARGS.get, DATE))
        pattern = re.compile(ARGS.get, re.IGNORECASE)

    for name, releases in RELEASES.items():
        if (ARGS.exact and name == ARGS.get) or (not ARGS.exact and pattern.match(name)):
            releases.get_releases()
            print(releases.markdown(name))
    exit(0)

print('\nGetting releases since {}\n---------------------------------\n'.format(DATE))

RESULT = '# Releases\n\n'

for name, releases in RELEASES.items():
    releases.get_releases()
    RESULT += releases.markdown(name)

print('Writing to releases.md...')

with open('releases.md', 'w') as f:
    f.write(RESULT)

print('Done!')
