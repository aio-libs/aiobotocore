### Description of Change
*Replace this text with why this change is required and how it was accomplished*

### Assumptions
*Replace this text with any assumptions made (if any)*

### Checklist for All Submissions
* [ ] I have added change info to [CHANGES.rst](https://github.com/aio-libs/aiobotocore/blob/master/CHANGES.rst)
* [ ] If this is resolving an issue (needed so future developers can determine if change is still necessary and under what conditions) (can be provided via link to issue with these details):
  * [ ] Detailed description of issue
  * [ ] Alternative methods considered (if any)
  * [ ] How issue is being resolved
  * [ ] How issue can be reproduced
* [ ] If this is providing a new feature  (can be provided via link to issue with these details):
  * [ ] Detailed description of new feature
  * [ ] Why needed
  * [ ] Alternatives methods considered (if any)

### Checklist when updating botocore and/or aiohttp versions

* [ ] I have read and followed [CONTRIBUTING.rst](https://github.com/aio-libs/aiobotocore/blob/master/CONTRIBUTING.rst#how-to-upgrade-botocore)
* [ ] I have updated test_patches.py where/if appropriate (also check if no changes necessary)
* [ ] I have ensured that the awscli/boto3 versions match the updated botocore version
* [ ] I have added URL to diff: https://github.com/boto/botocore/compare/[CURRENT_BOTO_VERSION_TAG]...[NEW_BOTO_VERSION_TAG]
