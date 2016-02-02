import asyncio
import sys
from botocore.exceptions import PaginationError
from botocore.paginate import PageIterator

PY_35 = sys.version_info >= (3, 5)


class AioPageIterator(PageIterator):

    def __init__(self, method, input_token, output_token, more_results,
                 result_keys, non_aggregate_keys, limit_key, max_items,
                 starting_token, page_size, op_kwargs):
        self._method = method
        self._op_kwargs = op_kwargs
        self._input_token = input_token
        self._output_token = output_token
        self._more_results = more_results
        self._result_keys = result_keys
        self._max_items = max_items
        self._limit_key = limit_key
        self._starting_token = starting_token
        self._page_size = page_size
        self._op_kwargs = op_kwargs
        self._resume_token = None
        self._non_aggregate_key_exprs = non_aggregate_keys
        self._non_aggregate_part = {}

        self._init_pager()

    @property
    def result_keys(self):
        return self._result_keys

    @property
    def resume_token(self):
        """Token to specify to resume pagination."""
        return self._resume_token

    @resume_token.setter
    def resume_token(self, value):
        if isinstance(value, list):
            self._resume_token = '___'.join([str(v) for v in value])

    @property
    def non_aggregate_part(self):
        return self._non_aggregate_part

    def __iter__(self):
        raise NotImplementedError

    def _init_pager(self):
        self._is_stop = False
        self._current_kwargs = self._op_kwargs
        self._previous_next_token = None
        self._next_token = [None for _ in range(len(self._input_token))]
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
            if all(t is None for t in self._next_token):
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

    if PY_35:  # pragma: no branch
        @asyncio.coroutine
        def __aiter__(self):
            return self

        @asyncio.coroutine
        def __anext__(self):
            if self._is_stop:
                raise StopAsyncIteration

            return self.next_page()

    def result_key_iters(self):
        raise NotImplementedError
        # teed_results = tee(self, len(self.result_keys))
        # return [ResultKeyIterator(i, result_key) for i, result_key in
        #         zip(teed_results, self.result_keys)]
