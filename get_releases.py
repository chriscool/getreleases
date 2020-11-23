#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Get various Git-related releases for Git Rev News.

This works by parsing a release page or a tag page for each supported software.

Currently supports : Git, Git for Windows, libgit2, libgit2sharp, GitHub
Enterprise, GitLab, Bitbucket, GitKraken, GitHub Desktop, tig, Sourcetree

This should be used like this:

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
from urllib.parse import urljoin
import requests

from bs4 import BeautifulSoup

PARSER = argparse.ArgumentParser()
PARSER.add_argument('-s', '--since', help='Get releases since that date. Format: YYYY-MM-DD. Default is 30 days before today.')
PARSER.add_argument('-u', '--user', help='GitHub API user (required for GitHub repos).')
PARSER.add_argument('-p', '--password', help='GitHub API password (required for GitHub repos).')
PARSER.add_argument('-l', '--list', help='List supported software and their URL.', action="store_true")

ARGS = PARSER.parse_args()

if ARGS.since:
    DATE = datetime.datetime.strptime(ARGS.since, '%Y-%m-%d').date()
else:
    DATE = datetime.date.today() - datetime.timedelta(days=30)

def get_date(string, fmt):
    string = re.sub(r'(\d)(th|st|nd)', '\\1', string)
    return datetime.datetime.strptime(string, fmt).date()

class Releases():

    def __init__(self):
        self._last_date = DATE
        self._releases = dict()

    def _fmt_releases(self, title, url='', replace_url=False):
        if not self._releases:
            return ''

        result = '+ {} '.format(title)
        fmt = '[{}]({})'

        for i, (version, href) in enumerate(self._releases.items()):
            if i > 0:
                result += ',\n'

            href = url if replace_url else urljoin(url, href)
            result += fmt.format(version, href)

        return result + '\n'

class HtmlPage(Releases):

    def __init__(self, url, pattern=r'(\d+\.\d+\.?\d*)'):
        Releases.__init__(self)
        self._pattern = pattern

        self._url = url

    def get_releases(self):
        self._soup = self._get_soup()
        self._pattern = re.compile(self._pattern, re.IGNORECASE)

    def _get_soup(self):
        print('> Requesting {}'.format(self._url))
        request = requests.get(self._url)

        if request.ok:
            soup = BeautifulSoup(request.text, 'html.parser')
        else:
            print('Error {}'.format(request.status_code))
            soup = None

        return soup

    def markdown(self, title):
        return self._fmt_releases(title, self._url)


class HtmlNestedPage(HtmlPage):

    def __init__(self, url, pattern=r'(\d+\.\d+\.?\d*)',
                 parent=None, date=None, releases=None):
        HtmlPage.__init__(self, url, pattern)

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

        for parent in parents:
            if self._date:
                dates = parent.find_all(*self._date['elt'])

                for date in dates:
                    string = date.text.strip()

                    try:
                        date = get_date(string, self._date['fmt'])
                    except ValueError:
                        continue

                    if date < self._last_date:
                        break

                    self._extract_releases(parent)

    def _extract_releases(self, element):
        if self._rel:
            self._extract_releases_with_elts(element)
        else:
            self._extract_releases_from_links(element)

    def _extract_releases_with_elts(self, element):
        rel = element.find(*self._rel['number'])

        if not rel:
            return

        relnum = re.search(self._pattern, rel.text)
        relhref = element.find(*self._rel['link'])

        if 'a' not in self._rel['link']:
            relhref = relhref.find('a')

        if relnum:
            self._releases.update({relnum.group(1): relhref.get('href')})

    def _extract_releases_from_links(self, element):
        links = element.find_all('a')

        if not links:
            return

        for link in links:
            link_match = re.search(self._pattern, link.text)

            if link_match:
                self._releases.update({link_match.group(1): link.get('href')})

class HtmlFlatPage(HtmlPage):

    def __init__(self, url, pattern=r'(\d+\.\d+\.?\d*)', releases=None, date=False):
        HtmlPage.__init__(self, url, pattern)

        self._date = date
        self._rel = releases

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

    def get_releases(self):
        HtmlPage.get_releases(self)

        nodes = self._soup.find_all(*self._rel['number'])

        for node in nodes:
            relnum = re.search(self._pattern, node.text)
            date = self._explore_next_nodes(node)

            if date and date < self._last_date:
                break

            if relnum:
                self._releases.update({relnum.group(1): self._url})

class GitHubTags(Releases):

    def __init__(self, repo, regex):
        Releases.__init__(self)

        self._api_user = ARGS.user
        self._api_pass = ARGS.password

        self._api = 'https://api.github.com/repos/' + repo + '/tags'
        self._url = 'https://github.com/' + repo + '/releases/tag/'
        self._repo = repo
        self._regex = re.compile(regex)

    def get_releases(self):
        print('> Getting releases from GitHub repo: {}'.format(self._repo))
        request = requests.get(self._api, auth=(self._api_user, self._api_pass))

        if not request.ok:
            print('Error {} while querying GitHub API'.format(request.status_code))
        else:
            json = request.json()

            for tag in json:
                tag_name = re.match(self._regex, tag['name'])
                if not tag_name:
                    continue

                if self._last_date:
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

                self._releases.update({version: self._url + tag['name']})

    def markdown(self, title, url='', replace_url=False):
        return self._fmt_releases(title, url, replace_url)

RELEASES = {
    'Git': HtmlNestedPage('https://public-inbox.org/git/?q=d%3A{:%Y%m%d}..+%5BANNOUNCE%5D+Git'.format(DATE),
                          pattern=r'^\[ANNOUNCE\] Git v?(\d\.\d+.*)'),
    'Git for Windows': GitHubTags('git-for-windows/git', r'^v(\d\.\d+\.\d+)\.windows\.(\d)$'),
    'libgit2': GitHubTags('libgit2/libgit2', r'^v(\d\.\d+\.\d+)$'),
    'libgit2sharp': GitHubTags('libgit2/libgit2sharp', r'^v(\d\.\d+\.?\d*)$'),
    'GitHub Enterprise': HtmlNestedPage('https://enterprise.github.com/releases/',
                                        parent=['h3'],
                                        date={'elt': ['small'], 'fmt': '%B %d, %Y'}),
    'GitLab': HtmlNestedPage('https://about.gitlab.com/blog/categories/releases/',
                             parent=['div', 'article'],
                             releases={'number': ['h3'], 'link': ['a', 'cover']},
                             date={'elt': ['div', 'date'], 'fmt': '%b %d, %Y'}),
    'Bitbucket Server': HtmlFlatPage('https://confluence.atlassian.com/bitbucketserver/bitbucket-server-release-notes-872139866.html',
                                     pattern=r'(\d\.\d+)',
                                     releases={'number': ['h2']},
                                     date={'elt': ['p', 'strong'],
                                           'pattern': '(.*)',
                                           'fmt': '%d %B %Y'}),
    'GitKraken': HtmlFlatPage('https://support.gitkraken.com/release-notes/current',
                              releases={'number': ['h2']},
                              date={'elt': ['p'],
                                    'pattern': r' - (.* \d{4})$',
                                    'fmt': '%A, %B %d, %Y'}),
    'GitHub Desktop': GitHubTags('desktop/desktop', r'^release-(\d\.\d\.\d+)$'),
    'Sourcetree': HtmlNestedPage('https://www.sourcetreeapp.com/download-archives',
                                 pattern=r'(\d\.\d\.?\d*\.?\d*)',
                                 parent=['tr'],
                                 releases={'number': ['div'], 'link': ['small']},
                                 date={'elt': ['td'], 'fmt': '%d-%b-%Y'}),
    'tig': HtmlNestedPage('https://public-inbox.org/git/?q=d%3A{:%Y%m%d}..+%5BANNOUNCE%5D tig'.format(DATE),
                          pattern=r'^\[ANNOUNCE\] tig-(.*)')
}

if ARGS.list:
    print("Supported software:")
    for name, releases in RELEASES.items():
        print('\t{} <-- {}'.format(name, releases._url))
    exit(0)

print('\nGetting releases since {}\n---------------------------------\n'.format(DATE))

RESULT = '# Releases\n\n'

for name, releases in RELEASES.items():
    releases.get_releases()
    if name == 'GitHub Desktop':
        RESULT += releases.markdown(name,
                                    url='https://desktop.github.com/release-notes/',
                                    replace_url=True)
    else:
        RESULT += releases.markdown(name)

print('Writing to releases.md...')

with open('releases.md', 'w') as f:
    f.write(RESULT)

print('Done!')
