# Convert numbers to/from base banana. See https://basebanana.org/
#
# Code from https://git.lattuga.net/itec/banana
#
# MIT License
#
# Copyright (c) 2020, itec
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Main module."""
import logging
import random

log = logging.getLogger("libbanana")


class Codec:
    def __init__(
        self,
        shiftalpha: int = 0,
        alphaend: int = 0,
        minlength: int = 0,
        alphabets: tuple[list[str], list[str]] | None = None,
    ) -> None:
        self.shiftalpha = shiftalpha
        self.alphaend = alphaend
        if alphabets is None:
            self.alphabets = (list("bcdfglmnprstvz"), list("aeiou"))
        else:
            self.alphabets = alphabets

    def encode(self, num: int, minlength: int = 1) -> str:
        alphabets = self.alphabets
        numalpha = len(alphabets)
        v = num
        st = ""
        length = 0

        idx = (numalpha - 1 + self.shiftalpha + self.alphaend) % numalpha
        while not (v == 0 and idx == (numalpha - 1 + self.shiftalpha) % numalpha and length >= minlength):
            r = v % len(alphabets[idx])
            v = int(v / len(alphabets[idx]))
            st = alphabets[idx][r] + st
            idx = (idx + numalpha - 1) % numalpha
            length += 1

        return st

    def decode(self, word: str) -> int:
        alphabets = self.alphabets

        numalpha = len(alphabets)
        if (len(word) - self.alphaend) % numalpha != 0:
            raise ValueError("Invalid banana")
        v = 0
        for i in range(len(word)):
            r = (numalpha + i + self.shiftalpha) % numalpha
            try:
                v = v * len(alphabets[r]) + alphabets[r].index(word[i])
            except (ValueError, KeyError):
                raise ValueError(f"Invalid character in position {i+1}")

        return v

    def is_valid(self, word: str) -> bool:
        alphabets = self.alphabets

        numalpha = len(alphabets)
        if (len(word) - self.alphaend) % numalpha != 0:
            return False
        for i in range(len(word)):
            r = (numalpha + i + self.shiftalpha) % numalpha
            if word[i] not in alphabets[r]:
                return False

        return True

    def random(self, minlength: int = 6, prng: random.Random = random.Random()) -> str:
        numalpha = len(self.alphabets)
        word = ""

        if minlength < 1:
            return ""

        curr_alpha = (numalpha - 1 + self.shiftalpha + self.alphaend) % numalpha
        final_alpha = (numalpha - 1 + self.shiftalpha) % numalpha
        while curr_alpha != final_alpha or len(word) < minlength:
            word = prng.choice(self.alphabets[curr_alpha]) + word
            curr_alpha = (curr_alpha - 1) % numalpha

        return word


class BananaCodec(Codec):
    def __init__(self) -> None:
        super().__init__()


if __name__ == "__main__":
    print("Hi I'm the basebanana library")
