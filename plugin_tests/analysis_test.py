#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
#  Copyright Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import httmock
from httmock import urlmatch, HTTMock
import json
import os
import sys
import time

# Need to set the environment variable before importing girder
girder_port = os.environ.get('GIRDER_TEST_PORT', '20200')
os.environ['GIRDER_PORT'] = girder_port# noqa

from tests import base
from girder_client import GirderClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../utility')))
import import_analyses

def setUpModule():
    """
    Enable the minerva plugin and start the server.
    """
    base.enabledPlugins.append('jobs')
    base.enabledPlugins.append('romanesco')
    base.enabledPlugins.append('gravatar')
    base.enabledPlugins.append('minerva')
    base.startServer(False)


def tearDownModule():
    """
    Stop the server.
    """
    base.stopServer()

class AnalysisTestCase(base.TestCase):
    """
    Tests of the minerva analysis functionality.
    """

    def setUp(self):
        """
        Set up the test case with  a user
        """
        super(AnalysisTestCase, self).setUp()

        self._user = self.model('user').createUser(
            'minervauser', 'password', 'minerva', 'user',
            'minervauser@example.com')

    def testAnalysisUtilityEndpoints(self):
        """ Test the minerva analysis utility endpoints.  """

        # at first there is no analysis folder or minerva collection

        path = '/minerva_analysis/folder'
        response = self.request(path=path, method='GET')
        self.assertStatus(response, 401)  # unauthorized

        response = self.request(path=path, method='GET', user=self._user)
        self.assertStatusOk(response)
        self.assertEquals(response.json['folder'], None, 'No analysis folder should exist')

        # create the analysis folder

        response = self.request(path=path, method='POST', user=self._user)
        self.assertStatusOk(response)
        self.assertNotEquals(response.json['folder'], None, 'An analysis folder should exist')

        # ensure we can get it

        response = self.request(path=path, method='GET', user=self._user)
        self.assertStatusOk(response)
        self.assertNotEquals(response.json['folder'], None, 'An analysis folder should exist')

    def testBsveSearchAnalysis(self):
        # create the analysis folder
        path = '/minerva_analysis/folder'
        response = self.request(path=path, method='POST', user=self._user)
        self.assertStatusOk(response)
        analyses_folder = response.json['folder']

        # import the bsve analysis
        client = GirderClient('localhost', girder_port)
        client.authenticate('minervauser', 'password')

        bsve_analysis_path = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), '../analyses/bsve'))
        import_analyses.import_analyses(client, bsve_analysis_path)

        path = '/item'
        params = {
            'folderId': analyses_folder['_id']
        }
        response = self.request(path=path, method='GET', params=params, user=self._user)
        self.assertStatusOk(response)
        self.assertEquals(len(response.json), 1, 'Expecting only one analysis')
        analysis = response.json[0]
        self.assertEquals(analysis['name'], 'bsve search', 'Expecting analysis name to be "bsve search"')
        expected_meta = {
            u'minerva': {
                u'analysis_type': u'bsve_search',
                u'analysis_name': u'bsve search',
                u'analysis_id': analysis['_id']
            }
        }
        self.assertEquals(analysis['meta'], expected_meta, 'Unexpected value for meta data')

        # create the dataset folder
        path = '/minerva_dataset/folder'
        params = {
            'userId': self._user['_id'],
        }
        response = self.request(path=path, method='POST', params=params, user=self._user)
        self.assertStatusOk(response)

        # mock the calls to bsve search
        @urlmatch(netloc=r'(.*\.)?beta-search.bsvecosystem.net(.*)$')
        def bsve_mock(url, request):
            if url.path.split('/')[-1] == 'request':
                return httmock.response(200, '12345')
            else:
                pluginTestDir = os.path.dirname(os.path.realpath(__file__))
                filepath = os.path.join(pluginTestDir, 'data', 'bsve_search.json')
                with open(filepath) as bsve_search_file:
                    content = {
                        'status': 1,
                        'results': json.load(bsve_search_file)
                    }
                    headers = {
                        'content-length': len(content),
                        'content-type': 'application/json'
                    }
                    return httmock.response(200, content, headers, request=request)

        with HTTMock(bsve_mock):
            response = self.request(
                path='/minerva_analysis/bsve_search',
                method='POST',
                params={
                    'datasetName': 'test dataset',
                    'bsveSearchParams': '{}'
                },
                user=self._user
            )

            # wait for the async job to complete
            searchResultsFinished = False
            count = 0
            while not searchResultsFinished and count < 5:
                # get the dataset and check if it has been updated
                path = '/minerva_dataset/%s/dataset' % str(response.json['dataset_id'])
                response = self.request(
                    path=path,
                    method='GET',
                    user=self._user
                )
                dataset = response.json
                if 'json_row' in dataset:
                    searchResultsFinished = True
                else:
                    time.sleep(2)
                    count += 1

            # ensure the first row of results was added to the dataset
            self.assertTrue('json_row' in dataset, 'json_row expected in dataset')
            self.assertTrue('data' in dataset['json_row'], 'data should be in json_row')
            self.assertTrue('Longitude' in dataset['json_row']['data'], 'data.Longitude should be in json_row')

            # ensure that we can map the Lat/Long to geojson, as this json has
            # unicode values for Lat/Long

            # update the minerva metadata with coordinate mapping
            metadata = {'minerva': dataset}
            metadata['minerva']['mapper'] = {
                "latitudeKeypath": "data.Latitude",
                "longitudeKeypath": "data.Longitude"
            }

            path = '/item/{}/metadata'.format(dataset['dataset_id'])
            response = self.request(
                path=path,
                method='PUT',
                user=self._user,
                body=json.dumps(metadata),
                type='application/json'
            )
            metadata = response.json

            # create geojson in the dataset
            path = '/minerva_dataset/{}/geojson'.format(dataset['dataset_id'])
            response = self.request(
                path=path,
                method='POST',
                user=self._user,
            )
            self.assertHasKeys(response.json, ['geojson_file'])
