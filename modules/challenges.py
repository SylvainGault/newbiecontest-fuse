# coding: utf-8

import errno
import copy
import time
import datetime
import re
import lxml.html

import fileobjects as fo
from authrequests import AuthException
from . import ParsingException, FSSubModuleFiles



class UnAuthFile(fo.File):
    def __init__(self, name, **kwargs):
        kwargs.setdefault('content', b"You are not authenticated !\n")
        super(UnAuthFile, self).__init__(name, **kwargs)



class VoteFile(fo.File):
    def __init__(self, name, chall, **kwargs):
        kwargs.setdefault('isWritable', True)
        super(VoteFile, self).__init__(name, **kwargs)
        self.chall = chall

    def write(self, buf, offset):
        length = len(buf)
        try:
            val = int(buf)
            if val < 0 or val > 10:
                return -errno.EINVAL
        except ValueError:
            val = buf.strip()
            if val != 'nothing':
                return -errno.EINVAL

        ret = self.chall.send_vote(val)
        if ret is not None:
            return ret

        self.content = str(val) + "\n"
        return length

    def truncate(path, length):
        # Never truncate
        return



class Challenge(FSSubModuleFiles):
    cachelife = 60
    unauthcachelife = 3
    namere = re.compile('(.*), par .*')
    lastvalidre = re.compile('Dernière validation par (.*), le (\d+/\d+/\d+ à \d+:\d+)')
    validsre = re.compile('(\d+) validation')
    ptsre = re.compile('(\d+) point')
    qualityre = re.compile('([0-9.]+) / 10')


    def __init__(self, req, name, url, status, valids, pts, quality, date):
        super(Challenge, self).__init__()
        self.req = req
        self.name = name
        self.url = url
        self.status = status
        self.valids = valids
        self.pts = pts
        self.quality = quality
        self.date = date
        self.cacheexpir = None


    def updatefiles(self):
        now = time.time()
        if self.cacheexpir is not None and self.cacheexpir > now:
            return

        self.files = {}

        fullurl = self.req.fullurl(self.url)
        self.files['url'] = fo.File('url', content = bytes(fullurl + "\n"))

        # Poor man's alternative to the real files gotten below
        self.files["status"] = fo.File("status", content = bytes(self.status) + "\n")
        self.files["name"] = fo.File("name", content = bytes(self.name) + "\n")
        self.files["validations"] = fo.File("validations", content = bytes(self.valids) + "\n")
        self.files["points"] = fo.File("points", content = bytes(str(self.pts)) + "\n")

        summary = "name: " + self.name + "\n"
        if self.status == 'devnull':
            summary += "status: /dev/null\n"
        elif self.status == 'nonvalid':
            summary += "status: Not validated\n"
        elif self.status == 'valid':
            summary += "status: Validated\n"
        else:
            summary += "status: Unknown\n"

        summary += "creation date: "
        summary += time.strftime("%Y/%m/%d", time.localtime(self.date))
        summary += "\n"
        summary += "challenge url: " + self.req.fullurl(self.url) + "\n"
        summary += "validation count: " + str(self.valids) + "\n"
        summary += "quality: " + str(self.quality) + " / 10\n"
        self.files["summary"] = fo.File("summary", content = bytes(summary))

        try:
            res = self.req.get(self.url, True)
        except AuthException:
            self.files['NotAuthenticated'] = UnAuthFile('NotAuthenticated')
            self.cacheexpir = now + self.unauthcachelife
            return

        doc = lxml.html.fromstring(res.content, base_url = res.url)
        [content] = doc.cssselect('div#content > div.textpad')

        # Get the status of the challenge
        [img] = content.cssselect('img[alt="Validation"]')
        statustitle = img.get('title')
        if u"supprimée" in statustitle:
            self.status = 'devnull'
        elif u"non validée" in statustitle:
            self.status = 'nonvalid'
        elif u"validée" in statustitle:
            self.status = 'valid'
        else:
            self.status = 'unknown'

        self.files["status"] = fo.File("status", content = bytes(self.status) + "\n")

        # Parse the challenge name
        h2 = content.cssselect('h2')
        self.name = lxml.html.tostring(h2[0], encoding = 'utf-8', method = 'text')
        self.name = self.name.rstrip("\r\n")
        match = self.namere.match(self.name)
        if match is not None:
            self.name = match.group(1)
        self.files["name"] = fo.File("name", content = bytes(self.name + "\n"))

        # Parse the author from the "name"
        links = h2[0].cssselect('a[href *= "page=info_membre"]')
        if len(links) > 0:
            self.author = lxml.html.tostring(links[0], encoding = 'utf-8', method = 'text')
            self.files["author"] = fo.File("author", content = bytes(self.author + "\n"))
        else:
            self.author = None

        # Parse number of validations
        # Note that self.valids is used to make the file "lastvalidation"
        self.valids = None
        for valids in content.xpath(u'.//*[contains(text(), "validation")]'):
            validstxt = lxml.html.tostring(valids, encoding = 'utf-8', method = 'text')
            match = self.validsre.match(validstxt)
            if match is not None:
                self.valids = int(match.group(1))
                break
        if self.valids is None:
            self.valids = 0

        # Parse nickname and date of last validation
        if not self.status == 'devnull' and self.valids > 0:
            [lastvalid] = content.xpath(u'.//*[contains(text(), "Dernière validation par")]')
            lastvalid = lxml.html.tostring(lastvalid, encoding = 'utf-8', method = 'text')
            match = self.lastvalidre.match(lastvalid)
            (lastvalidname, lastvaliddate) = match.groups()
            date = datetime.datetime.strptime(lastvaliddate, "%d/%m/%Y à %H:%M")
            lastvalidation = fo.File("lastvalidation", content = bytes(lastvalidname + "\n"))
            lastvalidation.stat.st_mtime = int(date.strftime("%s"))
            lastvalidation.stat.st_ctime = lastvalidation.stat.st_mtime
            self.files["lastvalidation"] = lastvalidation
        else:
            lastvalidation = None

        # Make the "validations" file
        validsfile = fo.File("validations", content = bytes(str(self.valids) + "\n"))
        # Copy the last validation date from lastvalidation if it exists
        if lastvalidation is not None:
            validsfile.stat.st_mtime = lastvalidation.stat.st_mtime
            validsfile.stat.st_ctime = validsfile.stat.st_mtime
        self.files["validations"] = validsfile

        # Parse the number of points
        if not self.status == 'devnull':
            # Some challenges are fucky
            for points in content.xpath(u'.//*[contains(text(), "point")]'):
                pts = lxml.html.tostring(points, encoding = 'utf-8', method = 'text')
                match = self.ptsre.match(pts)
                if match is not None:
                    self.pts = int(match.group(1))
            self.files["points"] = fo.File("points", content = bytes(str(self.pts)) + "\n")

        # Parse quality
        if not self.status == 'devnull':
            [img] = content.cssselect('img[src *= "challs_ranks"]')
            self.quality = img.get('title')
            match = self.qualityre.match(self.quality)
            self.quality = float(match.group(1))
            self.files["quality"] = fo.File("quality", content = bytes(str(self.quality)) + "\n")

        # Parse help url
        [link] = content.xpath('.//a[img/@alt="Aide"]')
        self.helpurl = link.get('href')
        self.helpurl = self.req.fullurl(self.helpurl)
        self.files["helpurl"] = fo.File("helpurl", content = bytes(self.helpurl + "\n"))

        # Parse afterwards url (if any)
        if self.status == 'valid':
            [link] = content.xpath('.//a[img/@alt="Afterwards"]')
            self.afterurl = link.get('href')
            self.afterurl = self.req.fullurl(self.afterurl)
            self.files["afterwardsurl"] = fo.File("afterwardsurl", content = bytes(self.afterurl + "\n"))
        else:
            self.afterurl = None

        # Parse the challenge description
        content2 = copy.deepcopy(content)
        # Remove everything up to (and including) the first <h2> element
        while len(content2) > 0 and content2[0].tag != 'h2':
            content2.remove(content2[0])
        if len(content2) > 0 and content2[0].tag == 'h2':
            content2.remove(content2[0])
        # Remove the end up to the second last <hr> if the challenged is not /dev/nulled
        if self.status != 'devnull':
            for _ in range(2):
                while len(content2) > 0 and content2[-1].tag != 'hr':
                    content2.remove(content2[-1])
                if len(content2) > 0 and content2[-1].tag == 'hr':
                    content2.remove(content2[-1])

        # Put the full HTML of the challenge in a file
        self.deschtml = lxml.html.tostring(content2)
        self.files["description.html"] = fo.File("description.html", content = bytes(self.deschtml + "\n"))

        content2.make_links_absolute()
        try:
            import html2text
            htmlcontent = lxml.html.tostring(content2, method = 'html')
            self.desc = html2text.html2text(htmlcontent).encode('utf-8')
        except ImportError:
            self.desc = lxml.html.tostring(content2, encoding = 'utf-8', method = 'text')

        self.files["description"] = fo.File("description", content = bytes(self.desc + "\n"))

        # Parse the vote
        if self.status == 'valid':
            [form] = content.cssselect('form[name *= "polling"]')
            self.voteurl = form.get('action')
            [option] = form.cssselect('option[selected]')
            self.vote = option.get('value')
            self.files["vote"] = VoteFile("vote", self, content = bytes(self.vote + "\n"))

        # Generate a challenge summary
        summary = "name: " + self.name + "\n"

        if self.author is not None:
            summary += "author: " + self.author + "\n"

        if self.status == 'devnull':
            summary += "status: /dev/null\n"
        elif self.status == 'nonvalid':
            summary += "status: Not validated\n"
        elif self.status == 'valid':
            summary += "status: Validated\n"
        else:
            summary += "status: Unknown\n"

        summary += "creation date: "
        summary += time.strftime("%Y/%m/%d", time.localtime(self.date))
        summary += "\n"
        summary += "challenge url: " + self.req.fullurl(self.url) + "\n"
        summary += "help url: " + self.helpurl + "\n"
        if self.afterurl is not None:
            summary += "afterwards url: " + self.afterurl + "\n"
        summary += "validation count: " + str(self.valids) + "\n"
        summary += "quality: " + str(self.quality) + " / 10\n"
        if self.vote != 'nothing':
            summary += "vote: " + str(self.vote) + " / 10\n"
        summary += "content:\n" + self.desc + "\n"
        self.files["summary"] = fo.File("summary", content = bytes(summary))


        self.cacheexpir = now + self.cachelife


    def getndirs(self):
        return 0


    def send_vote(self, vote):
        vote = str(vote)
        res = self.req.post(self.voteurl, data = {'note': vote})
        doc = lxml.html.fromstring(res.content, base_url = res.url)
        [content] = doc.cssselect('div#content > div.textpad')
        [h2] = content.cssselect('h2')
        msg = lxml.html.tostring(h2, encoding = 'utf-8', method = 'text')
        msg = msg.strip().lower()
        if msg.startswith("merci"):
            return None
        if msg.startswith("erreur"):
            return -errno.EINVAL
        # I dunno, LOL. ¯\_(ツ)_/¯
        return -errno.EIO



class Category(FSSubModuleFiles):
    cachelife = 60
    validsre = re.compile('^doGraph\((\d+),')
    ptsre = re.compile('^(\d+) point')
    votere = re.compile('^([0-9.]+) / 10')


    def __init__(self, req, url, nchalls):
        super(Category, self).__init__()
        self.req = req
        self.url = url
        self.nchalls = nchalls
        self.cacheexpir = None


    def updatefiles(self):
        now = time.time()
        if self.cacheexpir is not None and self.cacheexpir > now:
            return

        res = self.req.get(self.url)
        doc = lxml.html.fromstring(res.content, base_url = res.url)
        tables = doc.cssselect('div#content > div.textpad > table')

        # There might be table before the right one for the newest challenges
        table = tables[-1]

        self.dirmodules = {}

        for row in table.cssselect('tr'):
            # The first row only contains the column headers
            if len(row.cssselect('th')) > 0:
                continue

            [tdlink, tdvalids, tdpts, tdvote, tddate] = row.cssselect('td')

            # Parse link and challenge name
            [link] = tdlink.cssselect('a')
            challname = lxml.html.tostring(link, encoding = 'utf-8', method = 'text')
            challname = challname.replace('/', '_')
            challurl = link.get('href')

            # Parse validation status (validated, not validated, devnull)
            [img] = tdvalids.cssselect('img')
            statusimg = img.get('src')
            status = 'unknown'
            if 'nullvalide' in statusimg:
                status = 'devnull'
            elif 'nonvalide' in statusimg:
                status = 'nonvalid'
            elif 'valide' in statusimg:
                status = 'valid'

            # Parse validations
            [script] = tdvalids.cssselect('script')
            match = self.validsre.match(script.text)
            validscnt = int(match.group(1))

            # Parse points
            match = self.ptsre.match(tdpts.text)
            points = int(match.group(1))

            # Parse votes
            [img] = tdvote.cssselect('img')
            match = self.votere.match(img.get('title'))
            votes = float(match.group(1))

            # Parse date
            date = time.strptime(tddate.text, "%d/%m/%Y")
            date = time.mktime(date)

            self.dirmodules[challname] = Challenge(self.req, challname,
                    challurl, status, validscnt, points, votes, date)

        self.nchalls = len(self.dirmodules)
        self.cacheexpir = now + self.cachelife


    def getndirs(self):
        return self.nchalls



class Challenges(FSSubModuleFiles):
    urlcat = "index.php?page=challenges"
    cachelife = 60
    nchallsre = re.compile('^\d+ / (\d+)')


    def __init__(self, req):
        super(Challenges, self).__init__()
        self.req = req
        self.catexpir = None


    def updatefiles(self):
        now = time.time()
        if self.catexpir is not None and self.catexpir > now:
            return

        res = self.req.get(self.urlcat)
        doc = lxml.html.fromstring(res.content, base_url = res.url)
        tables = doc.cssselect('div#content > div.textpad > table')

        if len(tables) != 3:
            raise ParsingException()

        self.dirmodules = {}

        # Categories are linked in the first table
        tablecat = tables[0]

        for row in tablecat.cssselect('tr'):
            [tdlink, tdcount] = row.cssselect('td')

            # Parse the link and category name
            [link] = tdlink.cssselect('strong a')
            caturl = link.get('href')
            catname = lxml.html.tostring(link, encoding = 'utf-8', method = 'text').strip()

            if catname.startswith('Épreuves '):
                catname = catname[len('Épreuves '):]

            # Parse the challenge count
            nchalls = lxml.html.tostring(tdcount, encoding = 'utf-8', method = 'text')
            match = self.nchallsre.match(nchalls)
            nchalls = int(match.group(1))


            self.dirmodules[catname] = Category(self.req, caturl, nchalls)

        self.catexpir = now + self.cachelife
