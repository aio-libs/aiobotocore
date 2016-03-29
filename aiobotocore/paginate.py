import asyncio
import sys

from botocore.exceptions import PaginationError
from botocore.paginate import PageIterator
from botocore.utils import set_value_from_jmespath, merge_dicts

PY_35 = sys.version_info >= (3, 5)


class AioPageIterator(PageIterator):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_pager()

    def __iter__(self):
        raise NotImplementedError

    def _init_pager(self):
        self._is_stop = False
        self._current_kwargs = self._op_kwargs
        self._previous_next_token = None
        self._next_token = dict((key, None) for key in self._input_token)
        # The number of items from result_key we've seen so far.
        self._total_items = 0
        self._first_request = True
        self._primary_result_key = self.result_keys[0]
        self._starting_truncation = 0
        self._inject_starting_params(self._current_kwargs)

    @asyncio.coroutine
    def next_page(self):
        if self._is_stop:
            return None

        response = yield from self._make_request(self._current_kwargs)
        parsed = self._extract_parsed_response(response)
        if self._first_request:
            # The first request is handled differently.  We could
            # possibly have a resume/starting token that tells us where
            # to index into the retrieved page.
            if self._starting_token is not None:
                self._starting_truncation = self._handle_first_request(
                    parsed, self._primary_result_key,
                    self._starting_truncation)
            self._first_request = False
            self._record_non_aggregate_key_values(parsed)
        current_response = self._primary_result_key.search(parsed)
        if current_response is None:
            current_response = []
        num_current_response = len(current_response)
        truncate_amount = 0
        if self._max_items is not None:
            truncate_amount = (self._total_items + num_current_response) \
                - self._max_items

        if truncate_amount > 0:
            self._truncate_response(parsed, self._primary_result_key,
                                    truncate_amount, self._starting_truncation,
                                    self._next_token)
            self._is_stop = True
            return response
        else:
            self._total_items += num_current_response
            self._next_token = self._get_next_token(parsed)
            if all(t is None for t in self._next_token.values()):
                self._is_stop = True
                return response
            if self._max_items is not None and \
                    self._total_items == self._max_items:
                # We're on a page boundary so we can set the current
                # next token to be the resume token.
                self.resume_token = self._next_token
                self._is_stop = True
                return response
            if self._previous_next_token is not None and \
                    self._previous_next_token == self._next_token:
                message = ("The same next token was received "
                           "twice: %s" % self._next_token)
                raise PaginationError(message=message)
            self._inject_token_into_kwargs(self._current_kwargs,
                                           self._next_token)
            self._previous_next_token = self._next_token
            return response

    if PY_35:
        @asyncio.coroutine
        def __aiter__(self):
            return self

        @asyncio.coroutine
        def __anext__(self):
            if self._is_stop:
                raise StopAsyncIteration  # noqa

            return self.next_page()

    def result_key_iters(self):
        raise NotImplementedError
        # teed_results = tee(self, len(self.result_keys))
        # return [ResultKeyIterator(i, result_key) for i, result_key in
        #         zip(teed_results, self.result_keys)]

    @asyncio.coroutine
    def build_full_result(self):
        complete_result = {}
        while True:
            response = yield from self.next_page()
            if response is None:
                break
            page = response
            # We want to try to catch operation object pagination
            # and format correctly for those. They come in the form
            # of a tuple of two elements: (http_response, parsed_responsed).
            # We want the parsed_response as that is what the page iterator
            # uses. We can remove it though once operation objects are removed.
            if isinstance(response, tuple) and len(response) == 2:
                page = response[1]
            # We're incrementally building the full response page
            # by page.  For each page in the response we need to
            # inject the necessary components from the page
            # into the complete_result.
            for result_expression in self.result_keys:
                # In order to incrementally update a result key
                # we need to search the existing value from complete_result,
                # then we need to search the _current_ page for the
                # current result key value.  Then we append the current
                # value onto the existing value, and re-set that value
                # as the new value.
                result_value = result_expression.search(page)
                if result_value is None:
                    continue
                existing_value = result_expression.search(complete_result)
                if existing_value is None:
                    # Set the initial result
                    set_value_from_jmespath(
                        complete_result, result_expression.expression,
                        result_value)
                    continue
                # Now both result_value and existing_value contain something
                if isinstance(result_value, list):
                    existing_value.extend(result_value)
                elif isinstance(result_value, (int, float, str)):
                    # Modify the existing result with the sum or concatenation
                    set_value_from_jmespath(
                        complete_result, result_expression.expression,
                        existing_value + result_value)
        merge_dicts(complete_result, self.non_aggregate_part)
        if self.resume_token is not None:
            complete_result['NextToken'] = self.resume_token
        return complete_result
