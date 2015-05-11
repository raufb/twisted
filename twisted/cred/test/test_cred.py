# Copyright (c) Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Tests for L{twisted.cred}, now with 30% more starch.
"""

from __future__ import absolute_import, division

from zope.interface import implementer, Interface

from binascii import hexlify, unhexlify

from twisted.trial import unittest
from twisted.python.compat import nativeString, networkString
from twisted.python import components
from twisted.internet import defer
from twisted.cred import checkers, credentials, portal, error

try:
    from crypt import crypt
except ImportError:
    crypt = None

try:
    import PAM
    PAM
    from twisted.cred import pamauth
except ImportError:
    pamauth = None



class ITestable(Interface):
    pass



class TestAvatar:
    """
    A test avatar.
    """
    def __init__(self, name):
        self.name = name
        self.loggedIn = False
        self.loggedOut = False


    def login(self):
        assert not self.loggedIn
        self.loggedIn = True


    def logout(self):
        self.loggedOut = True



@implementer(ITestable)
class Testable(components.Adapter):
    pass

components.registerAdapter(Testable, TestAvatar, ITestable)



class IDerivedCredentials(credentials.IUsernamePassword):
    pass



@implementer(IDerivedCredentials, ITestable)
class DerivedCredentials(object):

    def __init__(self, username, password):
        self.username = username
        self.password = password


    def checkPassword(self, password):
        return password == self.password



@implementer(portal.IRealm)
class TestRealm:
    def __init__(self):
        self.avatars = {}


    def requestAvatar(self, avatarId, mind, *interfaces):
        if avatarId in self.avatars:
            avatar = self.avatars[avatarId]
        else:
            avatar = TestAvatar(avatarId)
            self.avatars[avatarId] = avatar
        avatar.login()
        return (interfaces[0], interfaces[0](avatar),
                avatar.logout)



class NewCredTests(unittest.TestCase):
    def setUp(self):
        r = self.realm = TestRealm()
        p = self.portal = portal.Portal(r)
        up = self.checker = checkers.InMemoryUsernamePasswordDatabaseDontUse()
        up.addUser(b"bob", b"hello")
        p.registerChecker(up)


    def testListCheckers(self):
        expected = [credentials.IUsernamePassword,
                    credentials.IUsernameHashedPassword]
        got = self.portal.listCredentialsInterfaces()
        self.assertEqual(sorted(got), sorted(expected))


    def testBasicLogin(self):
        l = []; f = []
        self.portal.login(credentials.UsernamePassword(b"bob", b"hello"),
                          self, ITestable).addCallback(
            l.append).addErrback(f.append)
        if f:
            raise f[0]
        iface, impl, logout = l[0]
        # whitebox
        self.assertEqual(iface, ITestable)
        self.failUnless(iface.providedBy(impl),
                        "%s does not implement %s" % (impl, iface))
        # greybox
        self.failUnless(impl.original.loggedIn)
        self.failUnless(not impl.original.loggedOut)
        logout()
        self.failUnless(impl.original.loggedOut)


    def test_derivedInterface(self):
        """
        Login with credentials implementing an interface inheriting from an
        interface registered with a checker (but not itself registered).
        """
        l = []
        f = []
        self.portal.login(DerivedCredentials(b"bob", b"hello"), self, ITestable
            ).addCallback(l.append
            ).addErrback(f.append)
        if f:
            raise f[0]
        iface, impl, logout = l[0]
        # whitebox
        self.assertEqual(iface, ITestable)
        self.failUnless(iface.providedBy(impl),
                        "%s does not implement %s" % (impl, iface))
        # greybox
        self.failUnless(impl.original.loggedIn)
        self.failUnless(not impl.original.loggedOut)
        logout()
        self.failUnless(impl.original.loggedOut)


    def testFailedLogin(self):
        l = []
        self.portal.login(credentials.UsernamePassword(b"bob", b"h3llo"),
                          self, ITestable).addErrback(
            lambda x: x.trap(error.UnauthorizedLogin)).addCallback(l.append)
        self.failUnless(l)
        self.assertEqual(error.UnauthorizedLogin, l[0])


    def testFailedLoginName(self):
        l = []
        self.portal.login(credentials.UsernamePassword(b"jay", b"hello"),
                          self, ITestable).addErrback(
            lambda x: x.trap(error.UnauthorizedLogin)).addCallback(l.append)
        self.failUnless(l)
        self.assertEqual(error.UnauthorizedLogin, l[0])



class OnDiskDatabaseTests(unittest.TestCase):
    users = [
        (b'user1', b'pass1'),
        (b'user2', b'pass2'),
        (b'user3', b'pass3'),
    ]

    def setUp(self):
        self.dbfile = self.mktemp()
        with open(self.dbfile, 'wb') as f:
            for (u, p) in self.users:
                f.write(u + b":" + p + b"\n")


    def testUserLookup(self):
        self.db = checkers.FilePasswordDB(self.dbfile)
        for (u, p) in self.users:
            self.failUnlessRaises(KeyError, self.db.getUser, u.upper())
            self.assertEqual(self.db.getUser(u), (u, p))


    def testCaseInSensitivity(self):
        self.db = checkers.FilePasswordDB(self.dbfile, caseSensitive=False)
        for (u, p) in self.users:
            self.assertEqual(self.db.getUser(u.upper()), (u, p))


    def testRequestAvatarId(self):
        self.db = checkers.FilePasswordDB(self.dbfile)
        creds = [credentials.UsernamePassword(u, p) for u, p in self.users]
        d = defer.gatherResults(
            [defer.maybeDeferred(self.db.requestAvatarId, c) for c in creds])
        d.addCallback(self.assertEqual, [u for u, p in self.users])
        return d


    def testRequestAvatarId_hashed(self):
        self.db = checkers.FilePasswordDB(self.dbfile)
        creds = [credentials.UsernameHashedPassword(u, p)
                 for u, p in self.users]
        d = defer.gatherResults(
            [defer.maybeDeferred(self.db.requestAvatarId, c) for c in creds])
        d.addCallback(self.assertEqual, [u for u, p in self.users])
        return d



class HashedPasswordOnDiskDatabaseTests(unittest.TestCase):
    users = [
        (b'user1', b'pass1'),
        (b'user2', b'pass2'),
        (b'user3', b'pass3'),
    ]

    def setUp(self):
        dbfile = self.mktemp()
        self.db = checkers.FilePasswordDB(dbfile, hash=self.hash)
        with open(dbfile, 'wb') as f:
            for (u, p) in self.users:
                f.write(u + b":" + self.hash(u, p, u[:2]) + b"\n")

        r = TestRealm()
        self.port = portal.Portal(r)
        self.port.registerChecker(self.db)


    def hash(self, u, p, s):
        return networkString(crypt(nativeString(p), nativeString(s)))


    def testGoodCredentials(self):
        goodCreds = [credentials.UsernamePassword(u, p) for u, p in self.users]
        d = defer.gatherResults([self.db.requestAvatarId(c)
                                 for c in goodCreds])
        d.addCallback(self.assertEqual, [u for u, p in self.users])
        return d


    def testGoodCredentials_login(self):
        goodCreds = [credentials.UsernamePassword(u, p) for u, p in self.users]
        d = defer.gatherResults([self.port.login(c, None, ITestable)
                                 for c in goodCreds])
        d.addCallback(lambda x: [a.original.name for i, a, l in x])
        d.addCallback(self.assertEqual, [u for u, p in self.users])
        return d


    def testBadCredentials(self):
        badCreds = [credentials.UsernamePassword(u, 'wrong password')
                    for u, p in self.users]
        d = defer.DeferredList([self.port.login(c, None, ITestable)
                                for c in badCreds], consumeErrors=True)
        d.addCallback(self._assertFailures, error.UnauthorizedLogin)
        return d


    def testHashedCredentials(self):
        hashedCreds = [credentials.UsernameHashedPassword(
            u, self.hash(None, p, u[:2])) for u, p in self.users]
        d = defer.DeferredList([self.port.login(c, None, ITestable)
                                for c in hashedCreds], consumeErrors=True)
        d.addCallback(self._assertFailures, error.UnhandledCredentials)
        return d


    def _assertFailures(self, failures, *expectedFailures):
        for flag, failure in failures:
            self.assertEqual(flag, defer.FAILURE)
            failure.trap(*expectedFailures)
        return None

    if crypt is None:
        skip = "crypt module not available"



class PluggableAuthenticationModulesTests(unittest.TestCase):

    def setUp(self):
        """
        Replace L{pamauth.callIntoPAM} with a dummy implementation with
        easily-controlled behavior.
        """
        self.patch(pamauth, 'callIntoPAM', self.callIntoPAM)


    def callIntoPAM(self, service, user, conv):
        if service != 'Twisted':
            raise error.UnauthorizedLogin('bad service: %s' % service)
        if user != 'testuser':
            raise error.UnauthorizedLogin('bad username: %s' % user)
        questions = [
                (1, "Password"),
                (2, "Message w/ Input"),
                (3, "Message w/o Input"),
                ]
        replies = conv(questions)
        if replies != [
            ("password", 0),
            ("entry", 0),
            ("", 0)
            ]:
                raise error.UnauthorizedLogin(
                    'bad conversion: %s' % repr(replies))
        return 1

    def _makeConv(self, d):
        def conv(questions):
            return defer.succeed([(d[t], 0) for t, q in questions])
        return conv

    def testRequestAvatarId(self):
        db = checkers.PluggableAuthenticationModulesChecker()
        conv = self._makeConv({1:'password', 2:'entry', 3:''})
        creds = credentials.PluggableAuthenticationModules('testuser',
                conv)
        d = db.requestAvatarId(creds)
        d.addCallback(self.assertEqual, 'testuser')
        return d

    def testBadCredentials(self):
        db = checkers.PluggableAuthenticationModulesChecker()
        conv = self._makeConv({1:'', 2:'', 3:''})
        creds = credentials.PluggableAuthenticationModules('testuser',
                conv)
        d = db.requestAvatarId(creds)
        self.assertFailure(d, error.UnauthorizedLogin)
        return d

    def testBadUsername(self):
        db = checkers.PluggableAuthenticationModulesChecker()
        conv = self._makeConv({1:'password', 2:'entry', 3:''})
        creds = credentials.PluggableAuthenticationModules('baduser',
                conv)
        d = db.requestAvatarId(creds)
        self.assertFailure(d, error.UnauthorizedLogin)
        return d

    if not pamauth:
        skip = "Can't run without PyPAM"



class CheckersMixin(object):
    """
    L{unittest.TestCase} mixin for testing that some checkers accept
    and deny specified credentials.

    Subclasses must provide
    - C{getCheckers} which returns a sequence of
      L{checkers.ICredentialChecker}
    - C{getGoodCredentials} which returns a list of 2-tuples of
      credential to check and avaterId to expect.
    - C{getBadCredentials} which returns a list of credentials
      which are expected to be unauthorized.
    """

    @defer.inlineCallbacks
    def test_positive(self):
        """
        The given credentials are accepted by all the checkers, and give
        the expected C{avatarID}s
        """
        for chk in self.getCheckers():
            for (cred, avatarId) in self.getGoodCredentials():
                r = yield chk.requestAvatarId(cred)
                self.assertEqual(r, avatarId)


    @defer.inlineCallbacks
    def test_negative(self):
        """
        The given credentials are rejected by all the checkers.
        """
        for chk in self.getCheckers():
            for cred in self.getBadCredentials():
                d = chk.requestAvatarId(cred)
                yield self.assertFailure(d, error.UnauthorizedLogin)



class HashlessFilePasswordDBMixin(object):
    credClass = credentials.UsernamePassword
    diskHash = None
    networkHash = staticmethod(lambda x: x)

    _validCredentials = [
        (b'user1', b'password1'),
        (b'user2', b'password2'),
        (b'user3', b'password3')]


    def getGoodCredentials(self):
        for u, p in self._validCredentials:
            yield self.credClass(u, self.networkHash(p)), u


    def getBadCredentials(self):
        for u, p in [(b'user1', b'password3'),
                     (b'user2', b'password1'),
                     (b'bloof', b'blarf')]:
            yield self.credClass(u, self.networkHash(p))


    def getCheckers(self):
        diskHash = self.diskHash or (lambda x: x)
        hashCheck = self.diskHash and (lambda username, password,
                                       stored: self.diskHash(password))

        for cache in True, False:
            fn = self.mktemp()
            fObj = open(fn, 'wb')
            for u, p in self._validCredentials:
                fObj.write(u + b":" + diskHash(p) + b"\n")
            fObj.close()
            yield checkers.FilePasswordDB(fn, cache=cache, hash=hashCheck)

            fn = self.mktemp()
            fObj = open(fn, 'wb')
            for u, p in self._validCredentials:
                fObj.write(diskHash(p) + b' dingle dongle ' + u + b'\n')
            fObj.close()
            yield checkers.FilePasswordDB(fn, b' ', 3, 0,
                                          cache=cache, hash=hashCheck)

            fn = self.mktemp()
            fObj = open(fn, 'wb')
            for u, p in self._validCredentials:
                fObj.write(b'zip,zap,' + u.title() + b',zup,'\
                           + diskHash(p) + b'\n',)
            fObj.close()
            yield checkers.FilePasswordDB(fn, b',', 2, 4, False,
                                          cache=cache, hash=hashCheck)




class LocallyHashedFilePasswordDBMixin(HashlessFilePasswordDBMixin):
    diskHash = staticmethod(lambda x: hexlify(x))



class NetworkHashedFilePasswordDBMixin(HashlessFilePasswordDBMixin):
    networkHash = staticmethod(lambda x: hexlify(x))

    class credClass(credentials.UsernameHashedPassword):
        def checkPassword(self, password):
            return unhexlify(self.hashed) == password



class HashlessFilePasswordDBCheckerTests(HashlessFilePasswordDBMixin,
                                         CheckersMixin, unittest.TestCase):
    pass



class LocallyHashedFilePasswordDBCheckerTests(LocallyHashedFilePasswordDBMixin,
                                              CheckersMixin,
                                              unittest.TestCase):
    pass



class NetworkHashedFilePasswordDBCheckerTests(NetworkHashedFilePasswordDBMixin,
                                              CheckersMixin,
                                              unittest.TestCase):
    pass