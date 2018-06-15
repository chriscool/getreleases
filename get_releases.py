#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Get various Git-related releases for Git Rev News.

Currently supports : Git, Git for Windows, libgit2, libgit2sharp, Github
Enterprise, Gitlab, Bitbucket, GitKraken, Github Desktop, tig
"""

import argparse
import datetime
import re
from urllib.parse import urljoin
import requests

from bs4 import BeautifulSoup

PARSER = argparse.ArgumentParser()
PARSER.add_argument('-s', '--since', help='Get releases since that date. Format: YYYY-MM-DD. Default is 30 days before today.')
PARSER.add_argument('-u', '--user', help='Github API user (required for Github repos).')
PARSER.add_argument('-p', '--password', help='Github API password (required for Github repos).')

ARGS = PARSER.parse_args()

TODAY = datetime.date.today()
DATE = TODAY - datetime.timedelta(days=30)
DATE = ARGS.since if ARGS.since else DATE.strftime('%Y-%m-%d')

GITHUB_API_USER = ARGS.user if ARGS.user else 'user'
GITHUB_API_PASS = ARGS.password if ARGS.password else 'password'

class Releases():

    def __init__(self, last_date, limit=10):
        self._last_date = last_date
        self._limit = limit

        self._releases = dict()

        if isinstance(last_date, str):
            self._process_date()

    def _process_date(self):
        date = [int(d) for d in self._last_date.split('-')]
        self._last_date = datetime.datetime(*date)

    def _get_releases(self):
        self._releases = dict()

    def _fmt_releases(self, title, url='', replace_url=False):
        if not self._releases:
            return ''

        result = '+ ' + title + ' '

        for i, (version, href) in enumerate(self._releases.items()):

            if i > 0:
                result += ', '

            result += '[v' + version + ']'

            if replace_url:
                result += '(' + url + ')'
            else:
                result += '(' + urljoin(url, href) + ')'

        return result + '\n'

    def markdown(self, title, url='', replace_url=False):
        self._get_releases()
        return self._fmt_releases(title, url, replace_url)


class HtmlPage(Releases):

    def __init__(self, url, last_date, limit=10):
        print('Getting releases from HTML page: {}'.format(url))

        Releases.__init__(self, last_date, limit)
        self._url = url
        self._soup = self._get_soup()

    def _get_soup(self):
        request = requests.get(self._url)

        if request.ok:
            soup = BeautifulSoup(request.text, 'html.parser')
        else:
            print('Error {} requesting {}'.format(request.status_code, self._url))
            soup = None

        return soup

    def markdown(self, title):
        self._get_releases()
        return self._fmt_releases(title, self._url)


class PublicInbox(HtmlPage):

    def __init__(self, regex, last_date, limit=10):
        self._regex = regex
        self._last_date = last_date
        self._process_date()

        url = 'https://public-inbox.org/git/?q='

        if last_date:
            url += 'd%3A'
            url += self._last_date.strftime('%Y%m%d')
            url += '..+'
        url += '%5BANNOUNCE%5D'

        HtmlPage.__init__(self, url, last_date, limit)

    def _get_releases(self):
        emails = self._soup.find_all('a', text=self._regex, limit=self._limit)

        if emails:
            for email in emails:
                email_text = re.search(self._regex, email.text)

                self._releases.update({email_text.group(1): email.get('href')})


class GithubEnterprise(HtmlPage):

    def __init__(self, last_date, limit=10):
        HtmlPage.__init__(self, 'https://enterprise.github.com/releases/',
                          last_date, limit)

    def _get_releases(self):
        h3s = self._soup.find_all('h3', limit=self._limit)

        for h3 in h3s:
            date = datetime.datetime.strptime(h3.find('small').text, '%B %d, %Y')

            if date < self._last_date:
                break

            link = h3.find('a')
            self._releases.update({link.text: link.get('href')})


class Gitlab(HtmlPage):

    def __init__(self, last_date, limit=10):
        HtmlPage.__init__(self,
                          'https://about.gitlab.com/blog/categories/releases/',
                          last_date, limit)

    def _get_releases(self):
        section = self._soup.find_all('div', 'articles')[0]
        links = section.find_all('div', 'article', limit=self._limit)

        for rel in links:
            if self._last_date:
                rel_date_str = rel.find_all('div', 'date')[0].text.strip()
                rel_date = datetime.datetime.strptime(rel_date_str, '%b %d, %Y')

                if rel_date < self._last_date:
                    break

            text = re.findall(r'(\d{2}\.\d(\.\d)?)', rel.h2.text)

            if not text:
                continue

            version = ''
            for j, stuff in enumerate(text):
                if j > 0:
                    version += ', ' + stuff[0]
                else:
                    version += stuff[0]

            self._releases.update({version: rel.a.get('href')})


class Bitbucket(HtmlPage):

    def __init__(self, last_date, limit=10):
        HtmlPage.__init__(self,
                          'https://confluence.atlassian.com/bitbucketserver/bitbucket-server-release-notes-872139866.html',
                          last_date, limit)

    def _get_releases(self):
        h2s = self._soup.find_all('h2', id=re.compile(r'^BitbucketServerreleasenotes'),
                                  limit=self._limit)

        for h2 in h2s:
            if self._last_date:
                rel_date = h2.next_sibling.next_sibling.strong.text
                rel_date = datetime.datetime.strptime(rel_date, '%d %B %Y')

                if rel_date < self._last_date:
                    break

            version = re.search(r'(\d\.\d)', h2.text).group(1)
            link = self._soup.find('a', rel='nofollow',
                                   text=re.compile(r'^Bitbucket Server ' + version))

            self._releases.update({version: link.get('href')})


class GitKraken(HtmlPage):

    def __init__(self, last_date, limit=10):
        HtmlPage.__init__(self,
                          'https://support.gitkraken.com/release-notes/current',
                          last_date, limit)

    def _get_releases(self):
        h2s = self._soup.find_all('h2', id=re.compile(r'^version-'),
                                  limit=self._limit)

        for h2 in h2s:
            if self._last_date:
                rel_date = h2.next_sibling.next_sibling.text
                rel_date = rel_date.split('-')[-1].strip()
                rel_date = re.sub(r'(\d)(th|st|nd)', '\\1', rel_date)
                rel_date = datetime.datetime.strptime(rel_date, '%A, %B %d, %Y')

                if rel_date < self._last_date:
                    break

            version = re.search(r'(\d.\d\.\d)', h2.text).group(1)

            self._releases.update({version: 'https://support.gitkraken.com/release-notes/current'})


class GithubTags(Releases):

    def __init__(self, user, password, repo, regex, last_date, limit=10):
        print('Getting releases from Github repo: {}'.format(repo))
        Releases.__init__(self, last_date, limit)

        self._api_user = user
        self._api_pass = password

        self._api = 'https://api.github.com/repos/' + repo + '/tags'
        self._url = 'https://github.com/' + repo + '/releases/tag/'
        self._regex = regex

        self._get_releases()

    def _get_releases(self):
        request = requests.get(self._api, auth=(self._api_user, self._api_pass))

        if not request.ok:
            print('Error {} while querying Github API'.format(request.status_code))
        else:
            json = request.json()
            results = dict()

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
                        commit_date = datetime.datetime.strptime(commit_date[0:10],
                                                                 '%Y-%m-%d')

                        if commit_date < self._last_date:
                            break

                try:
                    version = tag_name.group(1) + '(' + tag_name.group(2) + ')'
                except IndexError:
                    version = tag_name.group(1)

                self._releases.update({version: self._url + tag['name']})

print('\nGetting releases since {}\n---------------------------------\n'.format(DATE))

RELEASES = {
    'Git': PublicInbox(re.compile(r'^\[ANNOUNCE\] Git v(.*)$'), DATE),
    'Git for Windows': GithubTags(GITHUB_API_USER, GITHUB_API_PASS,
                                  'git-for-windows/git',
                                  re.compile(r'^v(\d\.\d+\.\d+)\.windows\.(\d)$'),
                                  DATE),
    'libgit2': GithubTags(GITHUB_API_USER, GITHUB_API_PASS, 'libgit2/libgit2',
                          re.compile(r'^v(\d\.\d+\.\d+)$'), DATE),
    'libgit2sharp': GithubTags(GITHUB_API_USER, GITHUB_API_PASS,
                               'libgit2/libgit2sharp',
                               re.compile(r'^v(\d\.\d+\.?\d*)$'), DATE),
    'Github Enterprise': GithubEnterprise(DATE),
    'Gitlab': Gitlab(DATE),
    'Bitbucket': Bitbucket(DATE),
    'GitKraken': GitKraken(DATE),
    'Github Desktop': GithubTags(GITHUB_API_USER, GITHUB_API_PASS,
                                 'desktop/desktop',
                                 re.compile(r'^release-(\d\.\d\.\d+)$'), DATE),
    'tig': PublicInbox(re.compile(r'^\[ANNOUNCE\] tig-(.*)$'), DATE)
}

RESULT = '# Releases\n\n'

print('Formatting releases...')

for name, releases in RELEASES.items():
    if name == 'Github Desktop':
        RESULT += releases.markdown(name,
                                    url='https://desktop.github.com/release-notes/',
                                    replace_url=True)
    else:
        RESULT += releases.markdown(name)

print('Writing to releases.md...')

with open('releases.md', 'w') as f:
    f.write(RESULT)

print('Done!')
